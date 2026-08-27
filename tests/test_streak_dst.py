"""DST-correct reading-day bucketing (DayCtx).

Regression for the seasonal streak bug: day bucketing applied the client's
*current* UTC offset (JS getTimezoneOffset) to all of history, so a session
started between 3 and 4 AM local time in the opposite DST regime landed one
day late. Real case: a 03:39 CET bedtime read on the night of 2026-01-08→09
(02:39:54 UTC), bucketed with the August CEST offset (-120), fell on Jan 9 —
leaving Jan 8 empty and cutting a 260-day streak down to 230.

With an IANA timezone name (`tz` query param, sent by the web app alongside
`tz_offset`) DayCtx buckets per row using the zone's real transition instants.
Offset-only clients (plugin, external API users) keep the old fixed-offset
behaviour bit-for-bit.
"""
from datetime import date, datetime, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.services import reconciled_reading as rr
from backend.services.reading_day import DayCtx

# The prod session: 2026-01-09 02:39:54 UTC == 03:39:54 CET — before the 4 AM
# rollover, so it belongs to Jan 8's reading day.
PROD_UTC = datetime(2026, 1, 9, 2, 39, 54)
PROD_EPOCH = int(PROD_UTC.replace(tzinfo=timezone.utc).timestamp())
SUMMER_OFFSET = -120  # what a Vienna browser sends in August (CEST)


def _session(db, user, book, started_at):
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=started_at,
                          ended_at=started_at, duration_seconds=600, pages_turned=10))


# ── Unit: Python-side bucketing ───────────────────────────────────────────────

def test_py_day_winter_session_with_summer_offset():
    aware = DayCtx(SUMMER_OFFSET, "Europe/Vienna")
    legacy = DayCtx(SUMMER_OFFSET)
    assert aware.py_day(PROD_EPOCH) == date(2026, 1, 8)     # fixed
    assert legacy.py_day(PROD_EPOCH) == date(2026, 1, 9)    # documented old behaviour


def test_unknown_tz_name_falls_back_to_fixed_offset():
    assert DayCtx(SUMMER_OFFSET, "Not/AZone").tz_name is None
    assert DayCtx(SUMMER_OFFSET, "Not/AZone").py_day(PROD_EPOCH) == date(2026, 1, 9)


def test_summer_session_agrees_in_both_modes():
    # 03:30 CEST on Jul 14 (01:30 UTC) — same regime as the query offset, so
    # tz-aware and fixed-offset bucketing must agree: the day before.
    e = int(datetime(2026, 7, 14, 1, 30, tzinfo=timezone.utc).timestamp())
    assert DayCtx(SUMMER_OFFSET, "Europe/Vienna").py_day(e) == date(2026, 7, 13)
    assert DayCtx(SUMMER_OFFSET).py_day(e) == date(2026, 7, 13)


# ── SQL: session (datetime column) and page-stat (epoch column) paths ─────────

def test_dt_day_sql_buckets_winter_session_correctly(db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Black Summoner")
    _session(db, user, book, PROD_UTC)
    db.flush()

    aware = DayCtx(SUMMER_OFFSET, "Europe/Vienna")
    legacy = DayCtx(SUMMER_OFFSET)
    got_aware = db.query(aware.dt_day(ReadingSession.started_at)).scalar()
    got_legacy = db.query(legacy.dt_day(ReadingSession.started_at)).scalar()
    assert got_aware == "2026-01-08"
    assert got_legacy == "2026-01-09"


def test_active_days_includes_the_bedtime_read_day(db, admin_user, make_book):
    """The exact prod shape: normal daytime sessions plus one 03:39 CET
    bedtime read as the only reading of Jan 8. Tz-aware active_days keeps
    Jan 8; the fixed-offset set loses it (the streak breaker)."""
    user, _ = admin_user
    book = make_book(title="Streak chain")
    for d in (5, 6, 7):
        _session(db, user, book, datetime(2026, 1, d, 12, 0, 0))
    _session(db, user, book, PROD_UTC)                     # Jan 8's only read
    _session(db, user, book, datetime(2026, 1, 9, 12, 0, 0))
    db.flush()

    aware_days = rr.active_days(db, user.id, DayCtx(SUMMER_OFFSET, "Europe/Vienna"), [])
    legacy_days = rr.active_days(db, user.id, DayCtx(SUMMER_OFFSET), [])

    assert date(2026, 1, 8) in aware_days
    assert {date(2026, 1, d) for d in (5, 6, 7, 8, 9)} <= aware_days
    assert date(2026, 1, 8) not in legacy_days             # the old gap


def test_epoch_day_pagestat_path(db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Imported history")
    db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=100,
                    start_time=PROD_EPOCH, duration_seconds=300, device="Kindle"))
    db.flush()

    aware = DayCtx(SUMMER_OFFSET, "Europe/Vienna")
    assert db.query(aware.epoch_day(PageStat.start_time)).scalar() == "2026-01-08"
    # covered book → page-stat days join the active-day set
    days = rr.active_days(db, user.id, aware, [book.id])
    assert date(2026, 1, 8) in days


# ── API: the tz param threads through and the expressions run ─────────────────

def test_stats_endpoints_accept_tz(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="API smoke")
    _session(db, user, book, PROD_UTC)
    db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=100,
                    start_time=PROD_EPOCH, duration_seconds=300, device="Kindle"))
    db.flush()

    for path in (
        "/api/stats?days=0&tz_offset=-120&tz=Europe/Vienna",
        "/api/home/stats?tz_offset=-120&tz=Europe/Vienna",
        "/api/stats/timeline?tz_offset=-120&tz=Europe/Vienna",
        "/api/stats/sessions?tz_offset=-120&tz=Europe/Vienna",
        f"/api/books/{book.id}/reading-stats?tz_offset=-120&tz=Europe/Vienna",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"

    # With only the bedtime session in the DB, the timeline must key that
    # reading to Jan 8 (tz-aware) — and must NOT contain a Jan 9 bucket.
    daily = client.get(
        "/api/stats/timeline?tz_offset=-120&tz=Europe/Vienna").json()
    blob = str(daily)
    assert "2026-01-08" in blob
    assert "2026-01-09" not in blob
