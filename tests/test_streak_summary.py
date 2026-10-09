"""Daily + weekly streak summary with dates (services/streaks.py)."""
from datetime import date, timedelta

from backend.services.streaks import streak_summary_from_dates, streaks_from_dates

TODAY = date(2026, 5, 20)  # a Wednesday


def _days(start: date, n: int) -> set[date]:
    return {start + timedelta(days=i) for i in range(n)}


def test_empty():
    s = streak_summary_from_dates(set(), TODAY)
    assert s.as_dict() == {
        "current_days": 0, "current_start": None, "longest_days": 0,
        "longest_start": None, "longest_end": None, "current_weeks": 0,
        "current_weeks_start": None, "longest_weeks": 0,
        "longest_weeks_start": None, "longest_weeks_end": None,
    }


def test_current_and_longest_with_dates():
    days = _days(date(2026, 4, 1), 10) | _days(date(2026, 5, 16), 5)  # 1-10 Apr, 16-20 May
    s = streak_summary_from_dates(days, TODAY)
    assert (s.current_days, s.current_start) == (5, date(2026, 5, 16))
    assert (s.longest_days, s.longest_start, s.longest_end) == (10, date(2026, 4, 1), date(2026, 4, 10))


def test_current_survives_until_today_is_over():
    # Read through yesterday, not yet today: still current.
    s = streak_summary_from_dates(_days(date(2026, 5, 15), 5), TODAY)
    assert s.current_days == 5
    # Last read two days ago: broken.
    s = streak_summary_from_dates(_days(date(2026, 5, 14), 5), TODAY)
    assert s.current_days == 0


def test_tie_goes_to_most_recent_run():
    days = _days(date(2026, 4, 1), 3) | _days(date(2026, 4, 10), 3)
    s = streak_summary_from_dates(days, TODAY)
    assert s.longest_start == date(2026, 4, 10)


def test_weekly_streak_one_day_per_week_is_enough():
    # One read every Saturday for 4 weeks, ending last Saturday (16 May).
    days = {date(2026, 5, 16) - timedelta(weeks=i) for i in range(4)}
    s = streak_summary_from_dates(days, TODAY)
    assert s.current_days == 0  # daily streak long gone
    assert s.current_weeks == 4  # this week hasn't been read yet, last week counts
    assert s.current_weeks_start == date(2026, 4, 20)  # Monday of the first week
    assert s.longest_weeks == 4
    assert s.longest_weeks_end == date(2026, 5, 17)  # Sunday of the last week


def test_weekly_streak_breaks_on_an_empty_week():
    days = {date(2026, 4, 25), date(2026, 5, 2), date(2026, 5, 16)}  # skips week of 4 May
    s = streak_summary_from_dates(days, TODAY)
    assert s.current_weeks == 1
    assert s.longest_weeks == 2


def test_agrees_with_legacy_counts():
    days = _days(date(2026, 3, 1), 7) | _days(date(2026, 5, 18), 3) | {date(2026, 4, 2)}
    s = streak_summary_from_dates(days, TODAY)
    assert (s.current_days, s.longest_days) == streaks_from_dates(days, TODAY)
