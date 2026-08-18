"""Query-time reconciliation of imported KOReader page-stats with live reading sessions.

Both sources can describe the *same* Kindle reading (the plugin POSTs live sessions AND
KOReader's statistics.sqlite3 records every page), so naively summing them double-counts.

Rule (per sitting): a device-origin live `reading_session` is *superseded* when imported
`ko_page_stats` describe the same reading — i.e. a page row of the same book starts inside
the session's window (`superseded_clause`, ± `OVERLAP_SLACK_SECONDS`). Superseded sessions
stay in the DB and the session log ("not counted") but drop out of every total; the
page-stats (idle-capped, page-accurate) carry that sitting instead. A live session no
page-stats overlap counts as itself — reading on a second device that never synced its
history, or reading synced live before the history backfill caught up (issue #181: the
old *per-book* rule discarded a phone's whole week of live sessions the moment a Kindle
uploaded two sittings of the same book).
Web-reader and manual-log sessions (`NON_DEVICE_SOURCES`) are invisible to KOReader's
history, so they are never superseded; replacing them silently discarded e.g. a
hand-logged paper session on a Kindle-synced book. Books with no page-stats fall back to
sessions entirely.

Invariant: when a user has no page-stats at all, `covered_book_ids` is empty and every
helper returns exactly what the session-only query would — so existing stats behaviour is
unchanged (and the existing test suite stays valid).

Each helper returns plain Python structures keyed the way `stats.py` needs, merging:
  - page-stat aggregates (for covered books), and
  - session aggregates for books NOT covered.
Session counts from page-stats use real gap-clustering: consecutive page rows of a
book belong to one session until a gap exceeds SESSION_GAP_SECONDS (mirrors how the
live recorder and KOReader's own stats view think about sessions). Clusters shorter
than MIN_SESSION_SECONDS are noise (a page flip while shelving) and aren't counted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Integer, and_, exists, func, or_
from sqlalchemy.orm import Session

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession

# ReadingSession.device values that can never be described by imported KOReader
# page-stats. Everything else (device names, NULL legacy rows) is device-origin.
NON_DEVICE_SOURCES = ("web", "web-reader", "manual")

# A live session and page-stats written by the same device share one clock, so a
# sitting's first page row starts within seconds of the session. The slack only
# absorbs write-ordering jitter around open/close — it is deliberately small so a
# second device's reading of the same book minutes later is NOT swallowed.
OVERLAP_SLACK_SECONDS = 120


def _epoch(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt else None


def covered_book_ids(db: Session, user_id: int) -> list[int]:
    """Book ids that have imported page-stats. Empty means "no reconciliation at
    all" (every helper is then the plain session query); non-empty is a cheap
    pre-filter — only sessions on these books can be superseded, so the
    per-row overlap probe is skipped for everything else."""
    return [r[0] for r in db.query(PageStat.book_id).filter(PageStat.user_id == user_id).distinct()]


def _rs_epoch(col):
    # ReadingSession datetimes are naive UTC; strftime('%s') reads them as UTC.
    return func.cast(func.strftime("%s", col), Integer)


def superseded_clause():
    """SQL predicate on ReadingSession: a device-origin live session whose reading
    is also described by imported page-stats — some `ko_page_stats` row of the
    same user+book starts inside [started_at - slack, ended_at + slack].

    Auto-correlates against the enclosing ReadingSession query. NULL device is
    legacy plugin data and counts as device-origin, like everywhere else.
    """
    start = _rs_epoch(ReadingSession.started_at)
    end = func.coalesce(
        _rs_epoch(ReadingSession.ended_at),
        start + func.coalesce(ReadingSession.duration_seconds, 0),
    )
    overlap = exists().where(
        PageStat.user_id == ReadingSession.user_id,
        PageStat.book_id == ReadingSession.book_id,
        PageStat.start_time >= start - OVERLAP_SLACK_SECONDS,
        PageStat.start_time <= end + OVERLAP_SLACK_SECONDS,
    )
    return and_(
        or_(ReadingSession.device.is_(None),
            ReadingSession.device.notin_(NON_DEVICE_SOURCES)),
        overlap,
    )


def counted_clause():
    """Negation of `superseded_clause`: sessions that contribute to totals."""
    return ~superseded_clause()


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
        # Only sessions on covered books can be superseded (cheap pre-filter);
        # for those, drop exactly the device sittings page-stats already describe.
        q = q.filter(or_(
            ReadingSession.book_id.notin_(covered),
            counted_clause(),
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


# A new session starts when the gap between consecutive page rows of the same
# book exceeds this (same 30-minute idea KOReader's stats plugin uses).
SESSION_GAP_SECONDS = 1800
# Clusters shorter than this are page-flip noise, mirroring the live
# recorder's minimum-session threshold.
MIN_SESSION_SECONDS = 10


def _cluster_rows(db, user_id: int, tzm: str,
                  cutoff: Optional[datetime], range_end: Optional[datetime],
                  book_id: Optional[int] = None,
                  min_secs: int = MIN_SESSION_SECONDS):
    """Gap-clustered page-stat sessions:
    (book_id, day, start, last_start, secs, pages, device).

    ``last_start`` is the cluster's final row's start_time — the inclusive upper
    bound for a range delete of exactly this cluster's rows (durations can
    overshoot past the next cluster's first row, so start+duration is unsafe
    as a boundary). ``device`` is MAX() over the cluster — display-only.

    ``min_secs`` drops sub-threshold clusters (page-flip noise) — the default
    for anything that COUNTS sessions. The session list passes 0: a 7-second
    accidental open still holds page-stat seconds (they're in the totals and
    draw an Activity bar), so it must be listable and deletable even though it
    is not a sitting worth counting.

    Pure SQL (SQLite window functions): number the breaks with LAG, run a
    cumulative sum for cluster ids, aggregate. The day is the cluster's START
    day in the user's timezone — a read across midnight is one session, on
    the day it began, exactly like a live-recorded session.
    """
    from sqlalchemy import text

    where, params = ["user_id = :uid"], {"uid": user_id, "tzm": tzm,
                                         "gap": SESSION_GAP_SECONDS,
                                         "min_secs": min_secs}
    ce, ee = _epoch(cutoff), _epoch(range_end)
    if ce is not None:
        where.append("start_time >= :ce"); params["ce"] = ce
    if ee is not None:
        where.append("start_time < :ee"); params["ee"] = ee
    if book_id is not None:
        where.append("book_id = :bid"); params["bid"] = book_id

    sql = text(f"""
        WITH ordered AS (
            SELECT book_id, start_time, duration_seconds, device,
                   CASE WHEN start_time - LAG(start_time) OVER w > :gap
                        THEN 1 ELSE 0 END AS brk
            FROM ko_page_stats
            WHERE {' AND '.join(where)}
            WINDOW w AS (PARTITION BY book_id ORDER BY start_time)
        ), clustered AS (
            SELECT book_id, start_time, duration_seconds, device,
                   SUM(brk) OVER (PARTITION BY book_id ORDER BY start_time
                                  ROWS UNBOUNDED PRECEDING) AS cluster_id
            FROM ordered
        )
        SELECT book_id,
               date(MIN(start_time), 'unixepoch', :tzm) AS day,
               MIN(start_time) AS start,
               MAX(start_time) AS last_start,
               SUM(duration_seconds) AS secs,
               COUNT(*) AS pages,
               MAX(device) AS device
        FROM clustered
        GROUP BY book_id, cluster_id
        HAVING SUM(duration_seconds) >= :min_secs
    """)
    return db.execute(sql, params).all()


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
        ps = _ps_filtered(db, user_id, cutoff, range_end).with_entities(
            func.coalesce(func.sum(PageStat.duration_seconds), 0),
            func.coalesce(func.count(PageStat.id), 0),
        ).one()
        secs += int(ps[0] or 0)
        pages += int(ps[1] or 0)
        # real sessions: gap-clustered, not one-per-(book, day)
        sessions += len(_cluster_rows(db, user_id, tzm, cutoff, range_end))
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
        # seconds/pages stay allocated to the day each page was read; session
        # COUNTS come from gap-clusters, attributed to the cluster's start day
        # (a read across midnight is one session, like a live-recorded one).
        day_groups = (
            _ps_filtered(db, user_id, start_dt, end_dt)
            .with_entities(
                _ps_day(tzm).label("day"),
                func.sum(PageStat.duration_seconds).label("secs"),
                func.count(PageStat.id).label("pages"),
            )
            .group_by("day").all()
        )
        for g in day_groups:
            e = out.setdefault(g.day, [0, 0, 0])
            e[0] += int(g.secs or 0); e[2] += int(g.pages or 0)
        for c in _cluster_rows(db, user_id, tzm, start_dt, end_dt):
            e = out.setdefault(c.day, [0, 0, 0])
            e[1] += 1
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
                func.count(PageStat.id).label("pages"),
            )
            .group_by(PageStat.book_id).all()
        )
        for r in rows2:
            e = out.setdefault(r.book_id, [0, 0, 0])
            e[0] += int(r.secs or 0); e[2] += int(r.pages or 0)
        for c in _cluster_rows(db, user_id, tzm, cutoff, range_end):
            e = out.setdefault(c.book_id, [0, 0, 0])
            e[1] += 1
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


def book_day_seconds(db, user_id, tzm, covered) -> dict[tuple[int, str], int]:
    """(book_id, 'YYYY-MM-DD') -> seconds, all time. For the reading timeline."""
    rs_day = func.date(ReadingSession.started_at, tzm)
    rows = (
        _rs_filtered(db, user_id, covered, None, None)
        .filter(ReadingSession.book_id.isnot(None))
        .with_entities(ReadingSession.book_id, rs_day.label("d"),
                       func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("secs"))
        .group_by(ReadingSession.book_id, "d").all()
    )
    out: dict[tuple[int, str], int] = {(r.book_id, r.d): int(r.secs or 0) for r in rows}
    if covered:
        rows2 = (
            _ps_filtered(db, user_id, None, None)
            .with_entities(PageStat.book_id, _ps_day(tzm).label("d"),
                           func.coalesce(func.sum(PageStat.duration_seconds), 0).label("secs"))
            .group_by(PageStat.book_id, "d").all()
        )
        for r in rows2:
            out[(r.book_id, r.d)] = out.get((r.book_id, r.d), 0) + int(r.secs or 0)
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
