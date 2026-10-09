"""Shared reading-streak computation.

Buckets sessions by the user's reading day (local day with a 4-hour rollover —
see ``backend/services/reading_day.py``), so a session started at 01:30 CEST
still counts toward the previous day's bedtime read.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.tome_sync import ReadingSession
# Re-exported for existing importers; the canonical home is reading_day.
from backend.services.reading_day import ROLLOVER_HOURS, DayCtx, date_modifier, effective_today  # noqa: F401


def streaks_from_dates(day_set: set[date], today: date) -> tuple[int, int]:
    if not day_set:
        return 0, 0
    current = 0
    d = today
    while d in day_set:
        current += 1
        d -= timedelta(days=1)
    if current == 0:
        d = today - timedelta(days=1)
        while d in day_set:
            current += 1
            d -= timedelta(days=1)
    sorted_days = sorted(day_set)
    longest = 1
    run = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return current, longest


def _runs(days: list[date], step: int) -> list[tuple[date, date]]:
    """Consecutive runs (start, end) over sorted ``days`` spaced ``step`` days apart."""
    runs: list[tuple[date, date]] = []
    for d in days:
        if runs and (d - runs[-1][1]).days == step:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


@dataclass
class StreakSummary:
    """Daily + weekly streaks with their dates.

    A streak is still "current" while its last day is today or yesterday (for
    weeks: this week or last week), so it doesn't read as broken before you've
    had a chance to read today. Ties for longest go to the most recent run.
    """
    current_days: int = 0
    current_start: date | None = None
    longest_days: int = 0
    longest_start: date | None = None
    longest_end: date | None = None
    current_weeks: int = 0
    current_weeks_start: date | None = None
    longest_weeks: int = 0
    longest_weeks_start: date | None = None
    longest_weeks_end: date | None = None

    def as_dict(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "current_days": self.current_days,
            "current_start": iso(self.current_start),
            "longest_days": self.longest_days,
            "longest_start": iso(self.longest_start),
            "longest_end": iso(self.longest_end),
            "current_weeks": self.current_weeks,
            "current_weeks_start": iso(self.current_weeks_start),
            "longest_weeks": self.longest_weeks,
            "longest_weeks_start": iso(self.longest_weeks_start),
            # Last day of the last week in the run (a Sunday).
            "longest_weeks_end": iso(self.longest_weeks_end),
        }


def streak_summary_from_dates(day_set: set[date], today: date) -> StreakSummary:
    s = StreakSummary()
    if not day_set:
        return s
    day_runs = _runs(sorted(day_set), 1)
    last = day_runs[-1]
    if (today - last[1]).days <= 1:
        s.current_days = (last[1] - last[0]).days + 1
        s.current_start = last[0]
    best = max(reversed(day_runs), key=lambda r: (r[1] - r[0]).days)
    s.longest_days = (best[1] - best[0]).days + 1
    s.longest_start, s.longest_end = best

    week_runs = _runs(sorted({_week_start(d) for d in day_set}), 7)
    wlast = week_runs[-1]
    if (_week_start(today) - wlast[1]).days <= 7:
        s.current_weeks = (wlast[1] - wlast[0]).days // 7 + 1
        s.current_weeks_start = wlast[0]
    wbest = max(reversed(week_runs), key=lambda r: (r[1] - r[0]).days)
    s.longest_weeks = (wbest[1] - wbest[0]).days // 7 + 1
    s.longest_weeks_start = wbest[0]
    s.longest_weeks_end = wbest[1] + timedelta(days=6)
    return s


def compute_user_streaks(
    db: Session,
    user_id: int,
    tz_offset_minutes: int,
    tz_name: str | None = None,
) -> tuple[int, int]:
    """Return (current_streak, longest_streak) for a user, in their local day with 4h rollover."""
    day = DayCtx(tz_offset_minutes, tz_name)
    rows = (
        db.query(day.dt_day(ReadingSession.started_at).label("d"))
        .filter(ReadingSession.user_id == user_id)
        .distinct()
        .all()
    )
    day_set = {date.fromisoformat(r.d) for r in rows if r.d}
    return streaks_from_dates(day_set, day.today())


def reconciled_user_streaks(
    db: Session,
    user_id: int,
    tz_offset_minutes: int,
    covered: list[int] | None = None,
    tz_name: str | None = None,
) -> tuple[int, int]:
    """Return (current, longest) streaks counting reconciled reading.

    When the user has imported KOReader page-stats, those days count toward the
    streak alongside live reading sessions; otherwise this is identical to
    ``compute_user_streaks``. This is the single source of truth so the home and
    stats endpoints can't drift apart. Pass ``covered`` to reuse an already-fetched
    covered-book-id list.
    """
    # Imported lazily to avoid a circular import (reconciled_reading pulls in models).
    from backend.services import reconciled_reading as rr

    if covered is None:
        covered = rr.covered_book_ids(db, user_id)
    if not covered:
        return compute_user_streaks(db, user_id, tz_offset_minutes, tz_name)
    day = DayCtx(tz_offset_minutes, tz_name)
    day_set = rr.active_days(db, user_id, day, covered)
    return streaks_from_dates(day_set, day.today())


def reconciled_streak_summary(
    db: Session,
    user_id: int,
    tz_offset_minutes: int,
    covered: list[int] | None = None,
    tz_name: str | None = None,
) -> StreakSummary:
    """Daily + weekly streaks with dates, over the same reconciled day set as
    ``reconciled_user_streaks`` (its current/longest day counts always agree)."""
    from backend.services import reconciled_reading as rr

    day = DayCtx(tz_offset_minutes, tz_name)
    if covered is None:
        covered = rr.covered_book_ids(db, user_id)
    if covered:
        day_set = rr.active_days(db, user_id, day, covered)
    else:
        rows = (
            db.query(day.dt_day(ReadingSession.started_at).label("d"))
            .filter(ReadingSession.user_id == user_id)
            .distinct()
            .all()
        )
        day_set = {date.fromisoformat(r.d) for r in rows if r.d}
    return streak_summary_from_dates(day_set, day.today())
