"""The canonical reading day (local day + 4h rollover) applies everywhere.

Regression for the recurring "forgot the rollover" bug class: streaks always
bucketed by ``reading_day.date_modifier`` while newer features (daily chart,
activity heatmap, re-reads, per-book timelines, momentum, DNA rhythm) buckets
drifted onto plain-UTC or plain-local days. One evening session crossing a
day boundary must never split into two days — or fabricate a re-read.
"""
from datetime import datetime, timedelta, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.services.reading_day import (
    date_modifier,
    effective_today,
    epoch_day_int,
)

CEST = -120  # JS getTimezoneOffset for UTC+2


def _epoch(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


def test_helpers_roll_midnight_sessions_back():
    # 01:30 local (CEST) = 23:30 UTC the day before +2h = 01:30 → rolls to previous day
    assert date_modifier(CEST) == "-2 hours"          # +2h tz, -4h rollover
    assert date_modifier(0) == "-4 hours"
    # Epoch at 01:30 local on Jun 2 (23:30 UTC Jun 1) → reading day Jun 1
    e = _epoch(2026, 6, 1, 23, 30)
    assert epoch_day_int(e, CEST) == epoch_day_int(_epoch(2026, 6, 1, 20), CEST)
    # ...but 05:00 local on Jun 2 is Jun 2
    assert epoch_day_int(_epoch(2026, 6, 2, 3), CEST) == epoch_day_int(e, CEST) + 1


def test_rereads_ignore_a_session_crossing_utc_midnight(client, db, admin_user, make_book):
    """A continuous 23:30–00:30 UTC evening read (UTC+2 user) is ONE reading day,
    so its pages are not re-reads. Under UTC-day bucketing every page dwelled on
    both sides of midnight counted as revisited."""
    user, _ = admin_user
    book = make_book(title="Evening Read")
    before = _epoch(2026, 6, 1, 23, 30)
    after = _epoch(2026, 6, 2, 0, 15)
    for p in range(1, 6):                     # pages 1-5 before midnight...
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=100,
                        start_time=before + p, duration_seconds=60, device="Kindle"))
    for p in range(1, 6):                     # ...and dwelled again just after
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=100,
                        start_time=after + p, duration_seconds=60, device="Kindle"))
    db.flush()

    rr = client.get(f"/api/stats?days=0&tz_offset={CEST}").json()["rereads"]["books"]
    assert book.id not in {b["book_id"] for b in rr}

    # A genuine revisit ten days later still registers.
    later = after + 10 * 86_400
    for p in range(1, 6):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=100,
                        start_time=later + p, duration_seconds=60, device="Kindle"))
    db.flush()
    rr = client.get(f"/api/stats?days=0&tz_offset={CEST}").json()["rereads"]["books"]
    assert book.id in {b["book_id"] for b in rr}


def test_per_book_timeline_one_day_across_midnight(client, db, admin_user, make_book):
    """The per-book reading log buckets a midnight-crossing device read into one
    reading day (one 'session'), not two.

    Page rows 20 minutes apart: within the 30-minute session gap, so the read
    is one gap-cluster — and one reading day despite crossing midnight."""
    user, _ = admin_user
    book = make_book(title="Night Owl Book")
    for i, ts in enumerate([_epoch(2026, 6, 1, 23, 50), _epoch(2026, 6, 2, 0, 10)]):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=i + 1, total_pages=100,
                        start_time=ts, duration_seconds=600, device="Kindle"))
    db.flush()

    own = client.get(f"/api/books/{book.id}/reading-stats?tz_offset={CEST}").json()["own"]
    assert own["sessions"] == 1
    assert len(own["session_timeline"]) == 1
    assert own["session_timeline"][0]["date"] == "2026-06-01"
    assert own["session_timeline"][0]["seconds"] == 1200


def test_web_session_timeline_uses_reading_day(client, db, admin_user, make_book):
    """Live web sessions bucket by the same reading day: a 01:30-local session
    belongs to the previous evening, matching the streak."""
    user, _ = admin_user
    book = make_book(title="Bedtime Web Read")
    # 23:30 UTC Jun 1 = 01:30 local Jun 2 (CEST) → reading day Jun 1
    when = datetime(2026, 6, 1, 23, 30)
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when,
                          ended_at=when + timedelta(minutes=30),
                          duration_seconds=1800, pages_turned=20, device="web-reader"))
    db.flush()

    own = client.get(f"/api/books/{book.id}/reading-stats?tz_offset={CEST}").json()["own"]
    assert [r["date"] for r in own["session_timeline"]] == ["2026-06-01"]


def test_daily_chart_and_heatmap_agree_with_streak(client, db, admin_user, make_book):
    """A midnight–4am-only reading day lands on the same date in the daily chart,
    the heatmap AND the streak (the original heatmap/streak drift bug)."""
    user, _ = admin_user
    book = make_book(title="After Midnight")
    # 01:00 local today (tz_offset=0) → reading day = yesterday's date... anchor
    # instead on the effective today so the streak is deterministic: read at
    # 01:00 local on the day after effective-today's calendar date.
    eff_today = effective_today(0)
    when = datetime(eff_today.year, eff_today.month, eff_today.day, 1, 0) + timedelta(days=1)
    if when > datetime.utcnow():
        # If 01:00 next-day hasn't happened yet in real time, use today 01:00
        # (which rolls to yesterday) — the invariant under test is identical.
        when -= timedelta(days=1)
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when,
                          ended_at=when + timedelta(minutes=45),
                          duration_seconds=2700, pages_turned=30, device="web-reader"))
    db.flush()

    data = client.get("/api/stats?days=30&tz_offset=0").json()
    streak_day = (when - timedelta(hours=4)).date().isoformat()
    daily_hit = [d["date"] for d in data["daily"] if d["seconds"] > 0]
    heatmap_hit = [d["date"] for d in data["heatmap_daily"] if d["seconds"] > 0]
    assert daily_hit == [streak_day]
    assert heatmap_hit == [streak_day]
    assert data["headline"]["current_streak_days"] >= 1


def test_dna_rhythm_counts_midnight_read_as_one_active_day(db, admin_user, make_book):
    """DNA rhythm's active-day set uses reading days: 20 midnight-crossing
    evening reads are 20 active days, not 40."""
    from backend.services import reconciled_reading as rr

    user, _ = admin_user
    book = make_book(title="DNA Book")
    base = _epoch(2026, 5, 1, 23, 30)
    for day in range(20):
        for ts in (base + day * 86_400, base + day * 86_400 + 3_000):  # straddles midnight UTC
            db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=100,
                            start_time=ts, duration_seconds=300, device="Kindle"))
    db.flush()

    covered = rr.covered_book_ids(db, user.id)
    days = rr.active_days(db, user.id, date_modifier(CEST), covered)
    assert len(days) == 20
