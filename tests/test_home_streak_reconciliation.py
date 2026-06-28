"""Home tab streak must reconcile imported KOReader page-stats, same as the stats page.

Regression for the home/stats streak drift: the home summary used a session-only
streak (e.g. 85) while the stats page counted page-stat days too (e.g. 166). Both
endpoints now route through ``reconciled_user_streaks``.
"""
from datetime import datetime, timedelta, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.services.streaks import effective_today


def _add_pagestat_day(db, user, book, eff_day):
    """One page-stat at 12:00 UTC on eff_day → maps to that effective reading day."""
    start = int(datetime(eff_day.year, eff_day.month, eff_day.day, 12, tzinfo=timezone.utc).timestamp())
    db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=100,
                    start_time=start, duration_seconds=300, device="Kindle"))


def test_home_streak_counts_pagestat_days_and_matches_stats(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Kindle History")

    # Three consecutive effective reading days ending today — page-stats only, no sessions.
    today = effective_today(0)
    for offset in range(3):
        _add_pagestat_day(db, user, book, today - timedelta(days=offset))
    db.flush()

    home = client.get("/api/home/stats?tz_offset=0").json()
    stats = client.get("/api/stats?days=0&tz_offset=0").json()["headline"]

    # Page-stat days feed the home streak (session-only would be 0 here)...
    assert home["current_streak_days"] == 3
    # ...and the two endpoints never disagree.
    assert home["current_streak_days"] == stats["current_streak_days"]


def _add_session_day(db, user, book, eff_day):
    """A live reading session at 12:00 UTC on eff_day (no page-stat)."""
    when = datetime(eff_day.year, eff_day.month, eff_day.day, 12)  # naive UTC
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when,
                          ended_at=when, duration_seconds=600, pages_turned=10))


def test_streak_counts_recent_sessions_on_a_covered_book(client, db, admin_user, make_book):
    """Regression (v1.7.0): a book with imported history became 'covered', which
    suppressed its live sessions in the reconciled active-day set — so reading it
    today/yesterday (web reader, or before the history sync caught up) dropped off
    the streak entirely. A day is active if you read at all, page-stat or session.
    """
    user, _ = admin_user
    book = make_book(title="Covered but read recently")
    today = effective_today(0)

    # Old imported history makes the book 'covered'.
    _add_pagestat_day(db, user, book, today - timedelta(days=20))
    # Read it today and yesterday — live sessions only, not yet in page-stats.
    _add_session_day(db, user, book, today)
    _add_session_day(db, user, book, today - timedelta(days=1))
    db.flush()

    home = client.get("/api/home/stats?tz_offset=0").json()
    stats = client.get("/api/stats?days=0&tz_offset=0").json()["headline"]
    assert home["current_streak_days"] == 2          # today + yesterday
    assert home["current_streak_days"] == stats["current_streak_days"]
