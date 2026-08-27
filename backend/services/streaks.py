"""Shared reading-streak computation.

Buckets sessions by the user's reading day (local day with a 4-hour rollover —
see ``backend/services/reading_day.py``), so a session started at 01:30 CEST
still counts toward the previous day's bedtime read.
"""
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
