"""Query-time reconciliation of imported KOReader page-stats with live reading sessions.

Both sources can describe the *same* Kindle reading (the plugin POSTs live sessions AND
KOReader's statistics.sqlite3 records every page), so naively summing them double-counts.

Rule (per book, per source): if a book has ANY imported `ko_page_stats`, those are
authoritative for its *device* reading time (idle-capped, full history) and its live
device-origin `reading_sessions` are ignored — they describe the same reading twice.
Web-reader and manual-log sessions (`NON_DEVICE_SOURCES`) are invisible to KOReader's
history, so they stay ADDITIVE even on covered books; replacing them silently discarded
e.g. a hand-logged paper session on a Kindle-synced book. Books with no page-stats fall
back to sessions entirely.

Invariant: when a user has no page-stats at all, `covered_book_ids` is empty and every
helper returns exactly what the session-only query would — so existing stats behaviour is
unchanged (and the existing test suite stays valid).

Each helper returns plain Python structures keyed the way `stats.py` needs, merging:
  - page-stat aggregates (for covered books), and
  - session aggregates for books NOT covered.
Sessions count from page-stats is approximated as one "session" per (book, local-day).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Integer, func, or_
from sqlalchemy.orm import Session

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession

# ReadingSession.device values that can never be described by imported KOReader
# page-stats. Everything else (device names, NULL legacy rows) is device-origin.
NON_DEVICE_SOURCES = ("web", "web-reader", "manual")


def _epoch(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt else None


def covered_book_ids(db: Session, user_id: int) -> list[int]:
    """Book ids that have imported page-stats (page-stats win for these books)."""
    return [r[0] for r in db.query(PageStat.book_id).filter(PageStat.user_id == user_id).distinct()]


def _ps_filtered(db: Session, user_id: int, cutoff: Optional[datetime], range_end: Optional[datetime]):
    q = db.query(PageStat).filter(PageStat.user_id == user_id)
    ce, ee = _epoch(cutoff), _epoch(range_end)
    if ce is not None:
        q = q.filter(PageStat.start_time >= ce)
    if ee is not None:
        q = q.filter(PageStat.start_time < ee)
    return q


def _rs_filtered(db: Session, user_id: int, covered: list[int],
                 cutoff: Optional[datetime], range_end: Optional[datetime]):
    q = db.query(ReadingSession).filter(ReadingSession.user_id == user_id)
    if covered:
        # Covered books drop only their device-origin sessions (page-stats
        # already describe that reading); web/manual sessions stay additive.
        q = q.filter(or_(
            ReadingSession.book_id.notin_(covered),
            ReadingSession.device.in_(NON_DEVICE_SOURCES),
        ))
    if cutoff is not None:
        q = q.filter(ReadingSession.started_at >= cutoff)
    if range_end is not None:
        q = q.filter(ReadingSession.started_at < range_end)
    return q


def _ps_day(tzm: str):
    return func.date(PageStat.start_time, "unixepoch", tzm)


def _ps_local(tzm: str):
    return func.datetime(PageStat.start_time, "unixepoch", tzm)


# ── Totals ────────────────────────────────────────────────────────────────────

def totals(db, user_id, tzm, covered, cutoff, range_end) -> tuple[int, int, int]:
    """(seconds, sessions, pages) reconciled over the window."""
    rs = _rs_filtered(db, user_id, covered, cutoff, range_end).with_entities(
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
        func.count(ReadingSession.id),
        func.coalesce(func.sum(ReadingSession.pages_turned), 0),
    ).one()
    secs, sessions, pages = int(rs[0] or 0), int(rs[1] or 0), int(rs[2] or 0)
    if covered:
        # page-stat day-groups: one "session" per (book, local-day)
        day_groups = (
            _ps_filtered(db, user_id, cutoff, range_end)
            .with_entities(
                PageStat.book_id, _ps_day(tzm).label("day"),
                func.sum(PageStat.duration_seconds).label("secs"),
                func.count(PageStat.id).label("pages"),
            )
            .group_by(PageStat.book_id, "day")
            .all()
        )
        secs += sum(int(g.secs or 0) for g in day_groups)
        pages += sum(int(g.pages or 0) for g in day_groups)
        sessions += len(day_groups)
    return secs, sessions, pages


# ── Daily (per local-day) ───────────────────────────────────────────────────────

def daily_map(db, user_id, tzm, covered, start_dt, end_dt) -> dict[str, tuple[int, int, int]]:
    """day_str -> (seconds, sessions, pages). Caller fills the gaps."""
    rows = (
        _rs_filtered(db, user_id, covered, start_dt, end_dt)
        .with_entities(
            func.date(ReadingSession.started_at, tzm).label("day"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by("day").all()
    )
    out: dict[str, list[int]] = {r.day: [int(r.secs or 0), int(r.sessions or 0), int(r.pages or 0)] for r in rows}
    if covered:
        # aggregate page-stats per (book, day) first, then roll up to day so "sessions"
        # = distinct books read that day.
        day_groups = (
            _ps_filtered(db, user_id, start_dt, end_dt)
            .with_entities(
                PageStat.book_id, _ps_day(tzm).label("day"),
                func.sum(PageStat.duration_seconds).label("secs"),
                func.count(PageStat.id).label("pages"),
            )
            .group_by(PageStat.book_id, "day").all()
        )
        for g in day_groups:
            e = out.setdefault(g.day, [0, 0, 0])
            e[0] += int(g.secs or 0); e[1] += 1; e[2] += int(g.pages or 0)
    return {d: (v[0], v[1], v[2]) for d, v in out.items()}


# ── Per-book ────────────────────────────────────────────────────────────────────

def book_seconds(db, user_id, tzm, covered, cutoff, range_end) -> dict[int, tuple[int, int, int]]:
    """book_id -> (seconds, sessions, pages) reconciled over the window."""
    rows = (
        _rs_filtered(db, user_id, covered, cutoff, range_end)
        .filter(ReadingSession.book_id.isnot(None))
        .with_entities(
            ReadingSession.book_id,
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by(ReadingSession.book_id).all()
    )
    out: dict[int, list[int]] = {r.book_id: [int(r.secs or 0), int(r.sessions or 0), int(r.pages or 0)] for r in rows}
    if covered:
        rows2 = (
            _ps_filtered(db, user_id, cutoff, range_end)
            .with_entities(
                PageStat.book_id,
                func.sum(PageStat.duration_seconds).label("secs"),
                func.count(func.distinct(_ps_day(tzm))).label("sessions"),
                func.count(PageStat.id).label("pages"),
            )
            .group_by(PageStat.book_id).all()
        )
        for r in rows2:
            e = out.setdefault(r.book_id, [0, 0, 0])
            e[0] += int(r.secs or 0); e[1] += int(r.sessions or 0); e[2] += int(r.pages or 0)
    return {b: (v[0], v[1], v[2]) for b, v in out.items()}


def book_month_seconds(db, user_id, tzm, covered, start_dt) -> dict[tuple[int, str], int]:
    """(book_id, 'YYYY-MM') -> seconds. For the genre-over-time stack."""
    rs_month = func.strftime("%Y-%m", func.datetime(ReadingSession.started_at, tzm))
    rows = (
        _rs_filtered(db, user_id, covered, start_dt, None)
        .filter(ReadingSession.book_id.isnot(None))
        .with_entities(ReadingSession.book_id, rs_month.label("m"),
                       func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"))
        .group_by(ReadingSession.book_id, "m").all()
    )
    out: dict[tuple[int, str], int] = {(r.book_id, r.m): int(r.secs or 0) for r in rows}
    if covered:
        ps_month = func.strftime("%Y-%m", _ps_local(tzm))
        rows2 = (
            _ps_filtered(db, user_id, start_dt, None)
            .with_entities(PageStat.book_id, ps_month.label("m"),
                           func.coalesce(func.sum(PageStat.duration_seconds), 0).label("secs"))
            .group_by(PageStat.book_id, "m").all()
        )
        for r in rows2:
            out[(r.book_id, r.m)] = out.get((r.book_id, r.m), 0) + int(r.secs or 0)
    return out


# ── Hour × day-of-week ──────────────────────────────────────────────────────────

def hour_dow(db, user_id, tzm, covered, cutoff, range_end) -> dict[tuple[int, int], tuple[int, int]]:
    """(dow, hour) -> (seconds, sessions)."""
    rs_local = func.datetime(ReadingSession.started_at, tzm)
    rows = (
        _rs_filtered(db, user_id, covered, cutoff, range_end)
        .with_entities(
            func.cast(func.strftime("%w", rs_local), Integer).label("dow"),
            func.cast(func.strftime("%H", rs_local), Integer).label("hour"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by("dow", "hour").all()
    )
    out: dict[tuple[int, int], list[int]] = {(r.dow, r.hour): [int(r.secs or 0), int(r.sessions or 0)] for r in rows}
    if covered:
        rows2 = (
            _ps_filtered(db, user_id, cutoff, range_end)
            .with_entities(
                func.cast(func.strftime("%w", _ps_local(tzm)), Integer).label("dow"),
                func.cast(func.strftime("%H", _ps_local(tzm)), Integer).label("hour"),
                func.coalesce(func.sum(PageStat.duration_seconds), 0).label("secs"),
                func.count(func.distinct(PageStat.book_id)).label("sessions"),
            )
            .group_by("dow", "hour").all()
        )
        for r in rows2:
            e = out.setdefault((r.dow, r.hour), [0, 0])
            e[0] += int(r.secs or 0); e[1] += int(r.sessions or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


# ── Monthly (per 'YYYY-MM') ─────────────────────────────────────────────────────

def monthly_map(db, user_id, tzm, covered, start_dt) -> dict[str, tuple[int, int]]:
    """'YYYY-MM' -> (seconds, sessions)."""
    rs_month = func.strftime("%Y-%m", func.datetime(ReadingSession.started_at, tzm))
    rows = (
        _rs_filtered(db, user_id, covered, start_dt, None)
        .with_entities(rs_month.label("m"),
                       func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"),
                       func.count(ReadingSession.id).label("sessions"))
        .group_by("m").all()
    )
    out: dict[str, list[int]] = {r.m: [int(r.secs or 0), int(r.sessions or 0)] for r in rows}
    if covered:
        day_groups = (
            _ps_filtered(db, user_id, start_dt, None)
            .with_entities(PageStat.book_id, _ps_day(tzm).label("day"),
                           func.sum(PageStat.duration_seconds).label("secs"))
            .group_by(PageStat.book_id, "day").all()
        )
        for g in day_groups:
            m = g.day[:7]
            e = out.setdefault(m, [0, 0])
            e[0] += int(g.secs or 0); e[1] += 1
    return {m: (v[0], v[1]) for m, v in out.items()}


# ── Reconciled active-day set (for streaks) ─────────────────────────────────────

def active_days(db, user_id, tzm, covered) -> set:
    from datetime import date as _date
    # A day is "active" if the user read at all that day — page-stat OR session,
    # covered book or not. We deliberately do NOT exclude covered-book sessions
    # here (unlike the time/count reconciliation, where the exclusion avoids
    # double-counting): for the active-DAY set, excluding them drops days you read
    # on a book that merely *has* imported history but whose page-stats don't (yet)
    # cover that day — e.g. recent web-reader reading, or reading synced before the
    # history sync caught up. That silently broke the streak after a first import.
    rs_days = {
        r[0] for r in
        _rs_filtered(db, user_id, [], None, None)
        .with_entities(func.date(ReadingSession.started_at, tzm)).distinct().all()
        if r[0]
    }
    if covered:
        rs_days |= {
            r[0] for r in
            _ps_filtered(db, user_id, None, None)
            .with_entities(_ps_day(tzm)).distinct().all()
            if r[0]
        }
    return {_date.fromisoformat(d) for d in rs_days}
