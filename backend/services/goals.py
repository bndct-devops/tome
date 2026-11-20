"""Reading goals: periods, progress, pace, history and suggested targets.

Everything a goal shows is computed here, from the same sources Stats uses:

- minutes and pages per day: ``reconciled_reading.daily_map`` — Stats' reconciled
  reading (imported KOReader page statistics win over the live sessions they
  describe) on Tome's reading day (local day with the 04:00 rollover, DST-correct
  with an IANA zone; see ``reading_day.DayCtx``);
- reading days: ``reconciled_reading.active_days``, the streak's day set (a few
  seconds in the web reader are not a day);
- books: ``user_book_status`` rows with status "read", dated by
  ``reading_stats.finished_date_expr()`` (this branch's rule: ``finished_at``,
  else ``updated_at`` for legacy rows), bucketed on the reading day.

Periods are calendar periods on reading days: day, week (starting on the
reader's ``week_start``: Monday or Sunday), month, year. "Now" is one instant
(``utcnow()``, patched by tests) turned into the reader's reading day once.

Pace: expected = target × full days passed ÷ days in the period (days before
today; day goals have none). "On course for" = floor(so far × days in the period
÷ full days passed), only once a quarter of the period has passed.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.library import BookType
from backend.models.notification import Notification
from backend.models.reading_goal import ReadingGoal, ReadingGoalTarget
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import reconciled_reading as rr
from backend.services.reading_day import DayCtx
from backend.services.reading_stats import finished_date_expr

# ── Kinds ─────────────────────────────────────────────────────────────────────

# kind -> (metric, period). metric/period stay plain strings on every goal: the
# iOS app decodes both as non-optional and one undecodable goal hides its card.
KINDS: dict[str, tuple[str, str]] = {
    "books_per_year": ("books", "year"),
    "books_per_month": ("books", "month"),
    "minutes_per_day": ("minutes", "day"),
    "minutes_per_week": ("minutes", "week"),
    "pages_per_day": ("pages", "day"),
    "pages_per_week": ("pages", "week"),
    "days_per_week": ("days", "week"),
    "finish_by": ("volumes", "deadline"),
}
# The six kinds of the first goals build; the others are new in 2026-11.
LEGACY_KINDS = ("books_per_year", "books_per_month", "minutes_per_day",
                "minutes_per_week", "pages_per_day", "pages_per_week")
NEW_KINDS = ("days_per_week", "finish_by")
# Kinds that can be limited to one book type.
TYPE_SCOPED_KINDS = set(LEGACY_KINDS)

UNITS = {"books": "books", "minutes": "min", "pages": "pages", "days": "days", "volumes": "volumes"}
# Fixed presets, for readers without enough history (the web dialog's chips).
PRESETS: dict[str, list[int]] = {
    "books_per_year": [12, 24, 52],
    "books_per_month": [1, 2, 4],
    "minutes_per_day": [15, 30, 60],
    "minutes_per_week": [120, 300, 600],
    "pages_per_day": [20, 50, 100],
    "pages_per_week": [150, 350, 700],
    "days_per_week": [3, 5, 7],
}
# Suggested targets are rounded down to this step.
STEPS = {
    "minutes_per_day": 5, "minutes_per_week": 10,
    "pages_per_day": 10, "pages_per_week": 50,
    "books_per_month": 1, "books_per_year": 1, "days_per_week": 1,
}

WEEK_STARTS = ("monday", "sunday")
# A deleted goal can be restored for this long; then it is purged.
RESTORE_WINDOW = timedelta(minutes=30)
# "On course for" is held back until this share of the period has passed.
PROJECTION_MIN_FRACTION = 0.25
# Past periods returned by the history by default (day goals: weeks of days).
HISTORY_PERIODS = {"day": 12, "week": 12, "month": 12, "year": 5}
# Day goals: "reached on N of the last 90 days".
DAY_SUMMARY_DAYS = 90
# Suggestions look at this many complete days / weeks / months before today.
SUGGEST_DAYS = 90
SUGGEST_WEEKS = 12
SUGGEST_MONTHS = 12
# Finish-by: "you've read N min a day over the last 30 days".
RECENT_DAYS = 30
# A month's finishes "share one day" when that day holds at least this many and
# more than half of them (finish dates set in bulk).
BULK_MIN = 3
# Average month length, for "N books a month".
DAYS_PER_MONTH = 30.44

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def utcnow() -> datetime:
    """The instant goals are computed for (naive UTC). Tests patch this."""
    return datetime.utcnow()


def parse_kind(kind: str) -> tuple[str, str]:
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f"Unknown goal kind {kind!r}") from None


# ── Periods ───────────────────────────────────────────────────────────────────

def _week_first_weekday(week_start: str) -> int:
    return 6 if week_start == "sunday" else 0


def period_bounds(period: str, d: date, week_start: str = "monday") -> tuple[date, date]:
    """First and last reading day (inclusive) of the period holding ``d``."""
    if period == "day":
        return d, d
    if period == "week":
        first = d - timedelta(days=(d.weekday() - _week_first_weekday(week_start)) % 7)
        return first, first + timedelta(days=6)
    if period == "month":
        first = d.replace(day=1)
        nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        return first, nxt - timedelta(days=1)
    if period == "year":
        return date(d.year, 1, 1), date(d.year, 12, 31)
    raise ValueError(f"No calendar period {period!r}")


def previous_period(period: str, start: date, week_start: str = "monday") -> tuple[date, date]:
    return period_bounds(period, start - timedelta(days=1), week_start)


def next_period(period: str, end: date, week_start: str = "monday") -> tuple[date, date]:
    return period_bounds(period, end + timedelta(days=1), week_start)


def _days(a: date, b: date) -> Iterable[date]:
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


# ── Names ─────────────────────────────────────────────────────────────────────

def _short_date(d: date, today: date) -> str:
    s = f"{MONTHS[d.month - 1][:3]} {d.day}"
    return s if d.year == today.year else f"{s}, {d.year}"


def goal_name(kind: str, period_start: Optional[date], type_label: Optional[str],
              today: date, finish_title: Optional[str] = None,
              due: Optional[date] = None) -> dict:
    """The one name a goal has everywhere: English ``name`` plus ``name_key`` and
    ``name_params`` for clients that translate (the key names the pattern, the
    params fill it). A book type is appended as " · <label>"."""
    params: dict = {}
    if kind == "books_per_year":
        params = {"year": period_start.year}
        name = f"Books in {period_start.year}"
    elif kind == "books_per_month":
        params = {"year": period_start.year, "month": period_start.month}
        name = f"Books in {MONTHS[period_start.month - 1]}"
    elif kind == "finish_by":
        params = {"title": finish_title, "due_date": due.isoformat() if due else None}
        name = f"{finish_title or 'Finish'} by {_short_date(due, today)}" if due else (finish_title or "Finish")
    else:
        name = {
            "minutes_per_day": "Minutes a day", "minutes_per_week": "Minutes a week",
            "pages_per_day": "Pages a day", "pages_per_week": "Pages a week",
            "days_per_week": "Reading days a week",
        }[kind]
    if type_label:
        params["book_type_label"] = type_label
        name = f"{name} · {type_label}"
    return {"name": name, "name_key": kind, "name_params": params}


# ── Per-request data ──────────────────────────────────────────────────────────

def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


class GoalData:
    """Everything one request needs, fetched once: the reader's today, their
    per-day reading (per book type), finishes and reading days."""

    def __init__(self, db: Session, user: User, day: DayCtx, now: Optional[datetime] = None):
        self.db = db
        self.user = user
        self.day = day
        self.now = now or utcnow()
        self.today = day.py_day(_epoch(self.now))
        self.week_start = user.week_start if user.week_start in WEEK_STARTS else "monday"
        self._covered: Optional[list[int]] = None
        self._reading: dict[Optional[int], tuple[Optional[date], dict[date, tuple[int, int, int]]]] = {}
        self._finishes: dict[Optional[int], dict[date, int]] = {}
        self._active: Optional[set[date]] = None
        self._type_books: dict[int, list[int]] = {}
        self._type_labels: dict[int, Optional[str]] = {}

    # sources ------------------------------------------------------------------

    @property
    def covered(self) -> list[int]:
        if self._covered is None:
            self._covered = rr.covered_book_ids(self.db, self.user.id)
        return self._covered

    def type_label(self, book_type_id: Optional[int]) -> Optional[str]:
        if book_type_id is None:
            return None
        if book_type_id not in self._type_labels:
            bt = self.db.get(BookType, book_type_id)
            self._type_labels[book_type_id] = bt.label if bt else None
        return self._type_labels[book_type_id]

    def _book_ids(self, book_type_id: Optional[int]) -> Optional[list[int]]:
        if book_type_id is None:
            return None
        if book_type_id not in self._type_books:
            self._type_books[book_type_id] = [
                r[0] for r in self.db.query(Book.id).filter(Book.book_type_id == book_type_id)
            ]
        return self._type_books[book_type_id]

    def reading(self, book_type_id: Optional[int], since: Optional[date]) -> dict[date, tuple[int, int, int]]:
        """Reading day -> (seconds, sessions, pages), Stats' reconciled counting,
        for every day from ``since`` (None: all time)."""
        have = self._reading.get(book_type_id)
        if have is not None and (have[0] is None or (since is not None and have[0] <= since)):
            return have[1]
        # A margin of two days around the UTC cut covers every zone and the
        # rollover; days outside the asked range are dropped by key.
        start_dt = datetime.combine(since - timedelta(days=2), time()) if since else None
        raw = rr.daily_map(self.db, self.user.id, self.day, self.covered, start_dt, None,
                           book_ids=self._book_ids(book_type_id))
        out = {}
        for k, v in raw.items():
            if not k:
                continue
            d = date.fromisoformat(k)
            if since is None or d >= since:
                out[d] = v
        self._reading[book_type_id] = (since, out)
        return out

    def finishes(self, book_type_id: Optional[int]) -> dict[date, int]:
        """Reading day -> books finished that day."""
        if book_type_id not in self._finishes:
            fin = finished_date_expr()
            d_expr = self.day.dt_day(fin)
            q = (
                self.db.query(d_expr.label("d"), func.count(UserBookStatus.id))
                .filter(UserBookStatus.user_id == self.user.id,
                        UserBookStatus.status == "read",
                        fin.isnot(None))
            )
            if book_type_id is not None:
                q = q.join(Book, Book.id == UserBookStatus.book_id).filter(Book.book_type_id == book_type_id)
            self._finishes[book_type_id] = {
                date.fromisoformat(d): int(n) for d, n in q.group_by("d").all() if d
            }
        return self._finishes[book_type_id]

    def active_days(self) -> set[date]:
        """The streak's reading days (all books)."""
        if self._active is None:
            self._active = rr.active_days(self.db, self.user.id, self.day, self.covered)
        return self._active

    # values -------------------------------------------------------------------

    def day_values(self, metric: str, book_type_id: Optional[int], since: Optional[date]) -> dict[date, float]:
        """Reading day -> the metric's value (minutes as float, pages, books,
        days as 0/1) for every day with any, from ``since``."""
        if metric == "books":
            return {d: n for d, n in self.finishes(book_type_id).items() if since is None or d >= since}
        if metric == "days":
            return {d: 1 for d in self.active_days() if since is None or d >= since}
        rd = self.reading(book_type_id, since)
        if metric == "minutes":
            return {d: v[0] / 60 for d, v in rd.items() if v[0]}
        return {d: v[2] for d, v in rd.items() if v[2]}

    def data_from(self, metric: str, book_type_id: Optional[int]) -> Optional[date]:
        """First reading day with any data for the metric (books: first finish date)."""
        vals = self.day_values(metric, book_type_id, None)
        return min(vals) if vals else None

    def local_midnight_utc(self, d: date) -> datetime:
        """UTC instant (naive) of local midnight starting ``d``: the notification key."""
        if self.day.tz_name:
            local = datetime.combine(d, time(), tzinfo=ZoneInfo(self.day.tz_name))
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        return datetime.combine(d, time()) + timedelta(minutes=self.day.tz_offset_minutes)


def _fmt(metric: str, v: float) -> float | int:
    """A value as goals show it: minutes to 0.1, everything else whole."""
    if metric == "minutes":
        return float(round(v, 1))
    return int(round(v))


def _sum(vals: dict[date, float], a: date, b: date) -> float:
    return sum(v for d, v in vals.items() if a <= d <= b)


# ── Targets over time ─────────────────────────────────────────────────────────

def target_rows(db: Session, goal: ReadingGoal) -> list[tuple[date, int]]:
    rows = (
        db.query(ReadingGoalTarget.changed_on, ReadingGoalTarget.target)
        .filter(ReadingGoalTarget.goal_id == goal.id)
        .order_by(ReadingGoalTarget.changed_on, ReadingGoalTarget.id)
        .all()
    )
    return [(r[0], int(r[1])) for r in rows] or [(goal_start(goal), goal.target)]


def target_for(rows: list[tuple[date, int]], period_end: date) -> int:
    """The target a period is judged by: the last one set on or before its last day."""
    applicable = [t for d, t in rows if d <= period_end]
    return applicable[-1] if applicable else rows[0][1]


def goal_start(goal: ReadingGoal) -> date:
    if goal.start_date:
        return goal.start_date
    return (goal.created_at or utcnow()).date()


# ── Current state ─────────────────────────────────────────────────────────────

def _week_strip(gd: GoalData, goal: ReadingGoal, metric: str, rows: list[tuple[date, int]]) -> list[dict]:
    """The reader's current calendar week, day by day: reached / missed / today
    (with its value so far) / future. For reading-days goals a day is reached
    when it is a reading day."""
    w0, w1 = period_bounds("week", gd.today, gd.week_start)
    vals = gd.day_values(metric, goal.book_type_id, w0)
    secs = gd.reading(goal.book_type_id if metric != "days" else None, w0)
    out = []
    for d in _days(w0, w1):
        v = vals.get(d, 0)
        if metric == "days":
            reached = bool(v)
            value: float | int = 1 if v else 0
        else:
            reached = v >= target_for(rows, d)
            value = _fmt(metric, v)
        state = "future" if d > gd.today else "today" if d == gd.today else ("reached" if reached else "missed")
        mark = {"date": d.isoformat(), "value": value, "reached": reached if d <= gd.today else False, "state": state}
        if metric in ("minutes", "days"):
            mark["seconds"] = int(secs.get(d, (0, 0, 0))[0])
        out.append(mark)
    return out


def goal_state(gd: GoalData, goal: ReadingGoal) -> dict:
    """The goal's current period, as cards (Stats and Home) draw it. A superset
    of the first goals build's fields with the same meanings (see API.md)."""
    if goal.kind == "finish_by":
        return finish_goal_state(gd, goal)
    metric, period = parse_kind(goal.kind)
    rows = target_rows(gd.db, goal)
    target = goal.target
    start, end = period_bounds(period, gd.today, gd.week_start)
    days_total = (end - start).days + 1
    days_passed = (gd.today - start).days
    days_left = (end - gd.today).days + 1
    vals = gd.day_values(metric, goal.book_type_id, start)
    raw = _sum(vals, start, gd.today)
    current = _fmt(metric, raw)
    reached = raw >= target
    expected = None if period == "day" else round(target * days_passed / days_total, 1)
    diff = None if expected is None else round(raw - target * days_passed / days_total)
    projection = None
    if period != "day" and days_passed > 0 and days_passed >= days_total * PROJECTION_MIN_FRACTION:
        projection = math.floor(raw * days_total / days_passed)
    if reached:
        status = "reached"
    elif diff is None:
        status = "open"
    else:
        status = "ahead" if diff >= 1 else "behind" if diff <= -1 else "on_pace"
    type_label = gd.type_label(goal.book_type_id)

    out: dict = {
        # ── fields of the first goals build (same names, same meanings) ──
        "id": goal.id,
        "kind": goal.kind,
        "metric": metric,
        "period": period,
        "target": target,
        "book_type_id": goal.book_type_id,
        "book_type_label": type_label,
        "current": current,
        "pct": round(raw / target * 100, 1) if target > 0 else 0.0,
        "expected": expected,
        # ── the redesign ──
        **goal_name(goal.kind, start, type_label, gd.today),
        "unit": UNITS[metric],
        "status": status,
        "reached": reached,
        "to_go": _fmt(metric, max(0.0, target - raw)),
        "diff": diff,
        "projection": projection,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "days_total": days_total,
        "days_passed": days_passed,
        "days_left": days_left,
        "start_date": goal_start(goal).isoformat(),
        "created_at": goal.created_at.isoformat() + "Z" if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() + "Z" if goal.updated_at else None,
    }
    if period == "day" or goal.kind == "days_per_week":
        strip = _week_strip(gd, goal, metric, rows)
        out["week"] = strip
        if period == "day":
            out["days_hit_this_week"] = sum(1 for m in strip if m["reached"])
    if goal.kind == "days_per_week":
        today_read = gd.today in gd.active_days()
        out["reachable"] = reached or (target - raw) <= days_left - (1 if today_read else 0)
    if period == "year":
        out["year"] = start.year
    return out


# ── Finish by a date ──────────────────────────────────────────────────────────

def _volume_rows(gd: GoalData, series: Optional[str], book_id: Optional[int]) -> list[Book]:
    from backend.core.permissions import book_visibility_filter

    q = gd.db.query(Book).filter(Book.status == "active")
    if series is not None:
        q = q.filter(Book.series == series)
    else:
        q = q.filter(Book.id == book_id)
    vis = book_visibility_filter(gd.db, gd.user)
    if vis is not True:
        q = q.filter(vis)
    return q.order_by(Book.series_index, Book.id).all()


def finish_estimate(gd: GoalData, series: Optional[str], book_id: Optional[int], due: date) -> dict:
    """What finishing a series (or one book) by ``due`` takes.

    Per remaining volume: the reader's own mean reconciled time per finished
    volume of that series (finished volumes with any time); without one, the
    backlog estimate for that book (word count at the reader's words a minute,
    else their mean per finished book of its type). A started volume counts
    only what is left of it: (1 - progress) × its time. Progress is the one
    Stats shows (best of the synced position and the furthest page-stat page).
    """
    from backend.api.stats import _book_progress, _page_progress
    from backend.models.tome_sync import TomeSyncPosition
    from backend.services import backlog_estimate as be

    books = _volume_rows(gd, series, book_id)
    ids = [b.id for b in books]
    status: dict[int, tuple[str, float]] = {}
    if ids:
        for bid, st, prog in (
            gd.db.query(UserBookStatus.book_id, UserBookStatus.status,
                        func.coalesce(TomeSyncPosition.percentage, UserBookStatus.progress_pct, 0.0))
            .outerjoin(TomeSyncPosition, (TomeSyncPosition.book_id == UserBookStatus.book_id)
                       & (TomeSyncPosition.user_id == UserBookStatus.user_id))
            .filter(UserBookStatus.user_id == gd.user.id, UserBookStatus.book_id.in_(ids))
        ):
            status[bid] = (st, prog or 0.0)
    page_pct = _page_progress(gd.db, gd.user.id, ids)
    secs = rr.book_seconds(gd.db, gd.user.id, gd.day, gd.covered, None, None) if ids else {}

    read = [b for b in books if status.get(b.id, ("unread", 0))[0] == "read"]
    left = [b for b in books if b not in read]
    progress = {}
    for b in left:
        st, synced = status.get(b.id, ("unread", 0.0))
        has_position = st == "reading" or synced or page_pct.get(b.id)
        p = _book_progress(synced, page_pct.get(b.id, 0.0)) / 100 if has_position else 0.0
        progress[b.id] = min(p, 0.999)

    fin_secs = [int(secs.get(b.id, (0, 0, 0))[0]) for b in read]
    fin_secs = [s for s in fin_secs if s > 0]
    per_volume = sum(fin_secs) // len(fin_secs) if (series is not None and fin_secs) else None
    basis = "series" if per_volume else None
    pace = None
    remaining: Optional[float] = 0.0
    unestimated = 0
    for b in left:
        est = per_volume
        if est is None:
            if pace is None:
                pace = be.compute_pace(gd.db, gd.user.id, gd.day.tz_offset_minutes, gd.day.tz_name)
            est, method = be.estimate_seconds(b, pace)
            if est is not None:
                basis = basis or f"backlog_{method}"
        if est is None:
            unestimated += 1
            continue
        remaining += (1 - progress[b.id]) * est
    if unestimated:
        remaining = None

    days_left = max(0, (due - gd.today).days + 1)
    recent = gd.reading(None, gd.today - timedelta(days=RECENT_DAYS))
    recent_secs = sum(v[0] for d, v in recent.items() if gd.today - timedelta(days=RECENT_DAYS) <= d < gd.today)
    needed = (remaining / days_left) if (remaining is not None and days_left > 0) else None
    title = series if series is not None else (books[0].title if books else None)
    started = [b for b in left if progress[b.id] > 0]
    return {
        "scope": "series" if series is not None else "book",
        "series": series,
        "book_id": book_id,
        "title": title,
        "due_date": due.isoformat(),
        "volumes_total": len(books),
        "volumes_read": len(read),
        "volumes_left": len(left),
        "progress_volumes": round(len(read) + sum(progress.values()), 3),
        "in_progress": [
            {"book_id": b.id, "title": b.title, "series_index": b.series_index,
             "progress": round(progress[b.id], 3)}
            for b in started
        ],
        "next_book_id": left[0].id if left else None,
        "per_volume_seconds": per_volume,
        "basis": basis,
        "remaining_seconds": round(remaining) if remaining is not None else None,
        "days_left": days_left,
        "needed_seconds_per_day": round(needed) if needed is not None else None,
        "recent_seconds_per_day": round(recent_secs / RECENT_DAYS),
        "recent_days": RECENT_DAYS,
    }


def finish_goal_state(gd: GoalData, goal: ReadingGoal) -> dict:
    due = goal.due_date or gd.today
    est = finish_estimate(gd, goal.target_series, goal.target_book_id if goal.target_series is None else None, due)
    total, done = est["volumes_total"], est["volumes_read"]
    complete = goal.completed_at is not None or (total > 0 and done >= total)
    expired = not complete and gd.today > due
    if complete:
        status = "completed"
    elif expired:
        status = "expired"
    elif est["needed_seconds_per_day"] is None:
        status = "open"
    else:
        # Home's rule: on pace while the last 30 days' average covers what it needs a day.
        status = "on_pace" if est["recent_seconds_per_day"] >= est["needed_seconds_per_day"] else "behind"
    return {
        "id": goal.id,
        "kind": goal.kind,
        "metric": "volumes",
        "period": "deadline",
        "target": total,
        "book_type_id": None,
        "book_type_label": None,
        "current": done,
        "pct": round(est["progress_volumes"] / total * 100, 1) if total else 0.0,
        "expected": None,
        **goal_name("finish_by", None, None, gd.today, est["title"], due),
        "unit": "volumes",
        "status": status,
        "reached": complete,
        "to_go": max(0, total - done),
        "diff": None,
        "projection": None,
        "period_start": goal_start(goal).isoformat(),
        "period_end": due.isoformat(),
        "days_total": (due - goal_start(goal)).days + 1,
        "days_passed": (gd.today - goal_start(goal)).days,
        "days_left": est["days_left"],
        "start_date": goal_start(goal).isoformat(),
        "created_at": goal.created_at.isoformat() + "Z" if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() + "Z" if goal.updated_at else None,
        "due_date": due.isoformat(),
        "target_series": goal.target_series,
        "target_book_id": goal.target_book_id,
        "completed_at": goal.completed_at.isoformat() + "Z" if goal.completed_at else None,
        "expired": expired,
        "finish": est,
    }


# ── Notifications ─────────────────────────────────────────────────────────────

def _phrase(goal: ReadingGoal, metric: str, period: str, state: dict) -> str:
    """'20 books in 2026 (Manga)', '2000 pages this week', 'reading on 5 days this week'."""
    t = goal.target
    if metric == "days":
        phrase = f"reading on {t} {'day' if t == 1 else 'days'} this week"
    else:
        noun = {"books": "books" if t != 1 else "book", "minutes": "minutes", "pages": "pages"}[metric]
        when = {"week": "this week", "month": "this month", "year": f"in {state.get('year')}"}[period]
        phrase = f"{t} {noun} {when}"
    if state.get("book_type_label"):
        phrase += f" ({state['book_type_label']})"
    return phrase


def maybe_notify(gd: GoalData, goal: ReadingGoal, state: dict) -> bool:
    """Create the goal_reached notification once per period (week, month and
    year goals, reading days a week) or once per finish-by goal when it is
    completed. Day goals never notify (a bell a day). Made when goals are
    read, as before: there is no hook on the reading itself yet."""
    if goal.kind == "finish_by":
        if state["status"] != "completed" or goal.completed_at is not None:
            return False
        goal.completed_at = gd.now
        body = f"You finished {state['finish']['title']} by {_short_date(goal.due_date or gd.today, gd.today)}."
    else:
        metric, period = parse_kind(goal.kind)
        if period == "day" or not state["reached"]:
            return False
        key = gd.local_midnight_utc(date.fromisoformat(state["period_start"]))
        if goal.notified_window_start == key:
            return False
        goal.notified_window_start = key
        body = f"You hit your goal of {_phrase(goal, metric, period, state)}."
    gd.db.add(Notification(
        user_id=goal.user_id,
        kind="goal_reached",
        title="Reading goal reached",
        body=body,
        link="/stats",
    ))
    return True


# ── History ───────────────────────────────────────────────────────────────────

def _median(xs: list[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _runs(flags: list[tuple[object, bool]]) -> tuple[int, Optional[object], Optional[object]]:
    """Longest run of True: (length, first key, last key); ties go to the latest."""
    best = (0, None, None)
    run, first = 0, None
    for key, ok in flags:
        if ok:
            run += 1
            first = key if run == 1 else first
            if run >= best[0]:
                best = (run, first, key)
        else:
            run = 0
    return best


def goal_history(gd: GoalData, goal: ReadingGoal, periods: Optional[int] = None) -> dict:
    """Past periods of a goal, each judged against the target it had then.

    Period state: ``met`` / ``missed``; ``before_goal`` (ended before the
    period the goal was set in: shown, not judged — ``reached`` says whether
    it would have been, against the first target); ``no_data`` (ended before
    the first day with any data for the metric: for books goals the first
    finish date); ``current`` (the period now running, never judged).
    """
    state = goal_state(gd, goal)
    base = {"goal_id": goal.id, "kind": goal.kind, "week_start": gd.week_start,
            "today": gd.today.isoformat(), "state": state}
    if goal.kind == "finish_by":
        return {**base, "periods": [], "weeks": None, "summary": None, "current": None}
    metric, period = parse_kind(goal.kind)
    rows = target_rows(gd.db, goal)
    data_from = gd.data_from(metric, goal.book_type_id)
    vals = gd.day_values(metric, goal.book_type_id, None)
    start = goal_start(goal)
    n = max(1, min(periods or HISTORY_PERIODS[period], 120))
    if period == "day":
        return {**base, **_day_history(gd, goal, metric, rows, vals, data_from, start, n)}

    goal_from = period_bounds(period, start, gd.week_start)[0]
    cur0, cur1 = period_bounds(period, gd.today, gd.week_start)

    def judge(p0: date, p1: date) -> dict:
        raw = _sum(vals, p0, p1)
        tgt = target_for(rows, p1)
        entry = {"start": p0.isoformat(), "end": p1.isoformat(), "value": _fmt(metric, raw), "target": tgt}
        if data_from is None or p1 < data_from:
            entry.update(state="no_data", reached=None)
        else:
            ok = raw >= tgt
            entry.update(reached=ok, state="before_goal" if p1 < goal_from else ("met" if ok else "missed"))
        if metric == "books":
            in_p = [(d, c) for d, c in vals.items() if p0 <= d <= p1]
            top = max(in_p, key=lambda x: (x[1], x[0]), default=None)
            if top and top[1] >= BULK_MIN and top[1] * 2 > raw:
                entry["bulk_day"], entry["bulk_count"] = top[0].isoformat(), int(top[1])
            else:
                entry["bulk_day"], entry["bulk_count"] = None, 0
        if metric == "minutes":
            entry["seconds"] = int(round(raw * 60))
        return entry

    # Every complete period since the data starts (for runs), the last n returned.
    complete: list[dict] = []
    first = period_bounds(period, data_from, gd.week_start)[0] if data_from else None
    back = []
    p0, p1 = previous_period(period, cur0, gd.week_start)
    while len(back) < n or (first is not None and p0 >= first):
        back.append((p0, p1))
        p0, p1 = previous_period(period, p0, gd.week_start)
    back.reverse()
    for a, b in back:
        complete.append(judge(a, b))
    shown = complete[-n:]
    judged = [p for p in complete if p["state"] != "no_data"]

    current = judge(cur0, cur1)
    current["state"] = "current"
    current["reached"] = bool(state["reached"])
    cum, run_v = [], 0.0
    for d in _days(cur0, gd.today):
        run_v += vals.get(d, 0)
        cum.append({"date": d.isoformat(), "value": _fmt(metric, vals.get(d, 0)), "cumulative": _fmt(metric, run_v)})
    current["days"] = cum
    current["expected"] = state["expected"]
    current["projection"] = state["projection"]

    shown_data = [p for p in shown if p["state"] != "no_data"]
    best_run = _runs([(p["start"], bool(p["reached"])) for p in judged])
    cur_run = 0
    for p in reversed(judged):
        if not p["reached"]:
            break
        cur_run += 1
    if state["reached"]:
        cur_run += 1
    best_p = max(shown_data, key=lambda p: (p["value"], p["start"]), default=None)
    summary = {
        "periods": len(shown),
        "with_data": len(shown_data),
        "reached": sum(1 for p in shown_data if p["reached"]),
        "met": sum(1 for p in shown if p["state"] == "met"),
        "missed": sum(1 for p in shown if p["state"] == "missed"),
        "before_goal": sum(1 for p in shown if p["state"] == "before_goal"),
        "usual": _median([p["value"] for p in shown_data]),
        "best": {"start": best_p["start"], "value": best_p["value"]} if best_p else None,
        "best_run": best_run[0],
        "best_run_start": best_run[1],
        "best_run_end": best_run[2],
        "current_run": cur_run,
        "current_run_includes_current": bool(state["reached"]),
        "data_from": data_from.isoformat() if data_from else None,
    }
    if metric == "books":
        summary["bulk"] = [{"start": p["start"], "day": p["bulk_day"], "count": p["bulk_count"], "of": p["value"]}
                           for p in shown if p.get("bulk_day")]
    return {**base, "periods": shown, "weeks": None, "summary": summary, "current": current}


def _day_history(gd: GoalData, goal: ReadingGoal, metric: str, rows, vals, data_from, start, n_weeks) -> dict:
    """Day goals: the last ``n_weeks`` calendar weeks day by day, and figures
    over the last 90 complete days (best run: since the data starts)."""
    w_last0, _ = period_bounds("week", gd.today, gd.week_start)
    weeks = []
    secs = gd.reading(goal.book_type_id, None) if metric == "minutes" else {}
    for k in range(n_weeks - 1, -1, -1):
        w0 = w_last0 - timedelta(days=7 * k)
        days = []
        for d in _days(w0, w0 + timedelta(days=6)):
            v = vals.get(d, 0)
            tgt = target_for(rows, d)
            mark = {"date": d.isoformat(), "value": _fmt(metric, v), "target": tgt}
            if metric == "minutes":
                mark["seconds"] = int(secs.get(d, (0, 0, 0))[0])
            if d > gd.today:
                mark.update(state="future", reached=None)
            elif d == gd.today:
                mark.update(state="today", reached=v >= tgt)
            elif data_from is None or d < data_from:
                mark.update(state="no_data", reached=None)
            else:
                ok = v >= tgt
                mark.update(reached=ok, state="before_goal" if d < start else ("met" if ok else "missed"))
            days.append(mark)
        weeks.append({"start": w0.isoformat(), "days": days})

    yesterday = gd.today - timedelta(days=1)
    window = [gd.today - timedelta(days=DAY_SUMMARY_DAYS - i) for i in range(DAY_SUMMARY_DAYS)]
    with_data = [d for d in window if data_from is not None and d >= data_from]
    hits = [d for d in with_data if vals.get(d, 0) >= target_for(rows, d)]
    flags = []
    if data_from is not None:
        flags = [(d, vals.get(d, 0) >= target_for(rows, d)) for d in _days(data_from, yesterday)]
    best = _runs(flags)
    cur_run = 0
    d = yesterday
    while data_from is not None and d >= data_from and vals.get(d, 0) >= target_for(rows, d):
        cur_run += 1
        d -= timedelta(days=1)
    today_reached = vals.get(gd.today, 0) >= target_for(rows, gd.today)
    if today_reached:
        cur_run += 1
    summary = {
        "days": DAY_SUMMARY_DAYS,
        "window_start": window[0].isoformat(),
        "window_end": window[-1].isoformat(),
        "with_data": len(with_data),
        "reached": len(hits),
        "met": sum(1 for x in hits if x >= start),
        "missed": sum(1 for x in with_data if x >= start and x not in set(hits)),
        "usual": _median([_fmt(metric, vals.get(x, 0)) for x in with_data]),
        "best_run": best[0],
        "best_run_start": best[1].isoformat() if best[1] else None,
        "best_run_end": best[2].isoformat() if best[2] else None,
        "current_run": cur_run,
        "current_run_includes_current": today_reached,
        "yesterday_value": _fmt(metric, vals.get(yesterday, 0)),
        "data_from": data_from.isoformat() if data_from else None,
    }
    return {"periods": [], "weeks": weeks, "summary": summary, "current": None}


# ── Suggested targets ─────────────────────────────────────────────────────────

def _pick(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def _floor_step(v: float, step: int) -> int:
    return max(step, int(step * (v // step)))


def _chips(values: list[float], step: int, cap: Optional[int] = None) -> list[dict]:
    """Three targets from the reader's own periods: the value reached in 3 of 4
    periods, in half of them and in 1 of 4 (25th / 50th / 75th percentile, the
    lower value at an index of q × n), rounded down to ``step`` and at least one
    step. Each says how many of those periods reached it."""
    srt = sorted(values)
    out = []
    for label, q in (("steady", 0.25), ("keep_it_up", 0.5), ("stretch", 0.75)):
        v = _floor_step(_pick(srt, q), step)
        if cap is not None:
            v = min(v, cap)
        out.append({"label": label, "value": v, "reached": sum(1 for x in values if x >= v), "of": len(values)})
    return out


def suggestions(gd: GoalData, kinds: Optional[list[str]] = None) -> dict:
    """Per kind: the reader's recent average, three suggested targets with how
    often each would have been reached, and the numbers for the consequence
    line ("at X min a day you'd finish about Y books a month"). ``suggested`` is
    null when there is too little history; ``presets`` (with the same counts
    when there is any history) are always there."""
    today = gd.today
    d_from = today - timedelta(days=SUGGEST_DAYS)
    days90 = [d_from + timedelta(days=i) for i in range(SUGGEST_DAYS)]
    rd = gd.reading(None, d_from - timedelta(days=7 * SUGGEST_WEEKS))
    fin = gd.finishes(None)
    active = gd.active_days()
    min90 = [rd.get(d, (0, 0, 0))[0] / 60 for d in days90]
    pages90 = [rd.get(d, (0, 0, 0))[2] for d in days90]
    books90 = sum(c for d, c in fin.items() if d_from <= d < today)
    per_book = {
        "minutes": (sum(min90) / books90) if books90 else None,
        "pages": (sum(pages90) / books90) if books90 else None,
    }

    w_cur0, _ = period_bounds("week", today, gd.week_start)
    weeks = [w_cur0 - timedelta(days=7 * k) for k in range(SUGGEST_WEEKS, 0, -1)]
    def week_sum(w0: date, idx: int) -> float:
        return sum(rd.get(w0 + timedelta(days=i), (0, 0, 0))[idx] for i in range(7))
    wk_min = [week_sum(w, 0) / 60 for w in weeks]
    wk_pages = [week_sum(w, 2) for w in weeks]
    wk_days = [sum(1 for i in range(7) if (w + timedelta(days=i)) in active) for w in weeks]

    m_cur0, _ = period_bounds("month", today)
    months = []
    p0 = m_cur0
    for _ in range(SUGGEST_MONTHS):
        p0, p1 = previous_period("month", p0)
        months.append((p0, p1))
    months.reverse()
    first_fin = min(fin) if fin else None
    month_counts = [sum(c for d, c in fin.items() if a <= d <= b) for a, b in months
                    if first_fin is not None and b >= first_fin]

    def books_a_month(v_per_day: float, metric: str) -> Optional[float]:
        pb = per_book[metric]
        return round(v_per_day * DAYS_PER_MONTH / pb, 2) if pb else None

    def minutes_a_day(books: float, days: float) -> Optional[float]:
        pb = per_book["minutes"]
        return round(books * pb / days, 1) if pb else None

    window = {"from": days90[0].isoformat(), "to": days90[-1].isoformat(), "days": SUGGEST_DAYS,
              "weeks_from": weeks[0].isoformat(), "weeks": SUGGEST_WEEKS,
              "months_from": months[0][0].isoformat(), "months": SUGGEST_MONTHS}
    out: dict = {}
    for kind in (kinds or [k for k in KINDS if k != "finish_by"]):
        if kind == "finish_by":
            continue
        metric, period = parse_kind(kind)
        step = STEPS[kind]
        cap = 7 if kind == "days_per_week" else None
        if kind in ("minutes_per_day", "pages_per_day"):
            values = min90 if metric == "minutes" else pages90
            have = sum(1 for v in values if v > 0) >= 14
            def conseq(v, metric=metric):
                return {"books_per_month": books_a_month(v, metric)}
            unit_count = "days"
        elif kind in ("minutes_per_week", "pages_per_week", "days_per_week"):
            values = {"minutes": wk_min, "pages": wk_pages, "days": wk_days}[metric]
            have = sum(1 for v in values if v > 0) >= 4
            def conseq(v, metric=metric):
                if metric == "days":
                    return {}
                return {"books_per_month": books_a_month(v / 7, metric)}
            unit_count = "weeks"
        elif kind == "books_per_month":
            values = month_counts
            have = len(values) >= 3 and sum(values) >= 3
            def conseq(v):
                return {"minutes_per_day": minutes_a_day(v, DAYS_PER_MONTH)}
            unit_count = "months"
        else:  # books_per_year: the months' rates, a year of them
            values = [c * 12 for c in month_counts]
            have = len(values) >= 3 and sum(month_counts) >= 3
            def conseq(v):
                return {"minutes_per_day": minutes_a_day(v, 365)}
            unit_count = "months"
            step = 5 if values and _pick(sorted(values), 0.5) >= 40 else 1
        avg = (sum(values) / len(values)) if values else None
        entry = {
            "kind": kind, "unit": UNITS[metric], "over": unit_count, "count": len(values),
            "average": round(avg, 1) if avg is not None else None,
            "median": round(_pick(sorted(values), 0.5), 1) if values else None,
            "suggested": None,
            "presets": [
                {"value": p, "reached": sum(1 for x in values if x >= p) if values else None,
                 "of": len(values), **conseq(p)}
                for p in PRESETS[kind]
            ],
        }
        if have:
            entry["suggested"] = [{**c, **conseq(c["value"])} for c in _chips(values, step, cap)]
        out[kind] = entry
    return {"today": today.isoformat(), "window": window, "per_book": {
        "books": books90,
        "minutes": round(per_book["minutes"], 1) if per_book["minutes"] else None,
        "pages": round(per_book["pages"], 1) if per_book["pages"] else None,
    }, "kinds": out}
