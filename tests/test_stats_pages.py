"""The designed Stats pages' data: /stats/habits, /stats/library, /stats/highlights,
the session list's counted filter and day totals, one progress value per book,
series next volumes and rated-series book counts."""
from datetime import datetime, timedelta, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import Annotation, ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.models.user_series_rating import UserSeriesRating

DAY = 86_400
NOW = datetime.utcnow().replace(microsecond=0)


def _session(db, user, book, started_at, secs, pages=10, device="web"):
    s = ReadingSession(user_id=user.id, book_id=book.id, started_at=started_at,
                       ended_at=started_at + timedelta(seconds=secs), duration_seconds=secs,
                       pages_turned=pages, device=device)
    db.add(s)
    db.flush()
    return s


def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def test_habits_lengths_longest_and_last_14_days(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Habit Book")
    # 10m, 20m, 45m, 90m, 150m: one per bucket; all at midday so the reading day is the calendar day.
    noon = NOW.replace(hour=12, minute=0, second=0)
    for i, mins in enumerate((10, 20, 45, 90, 150)):
        _session(db, user, book, noon - timedelta(days=i + 1), mins * 60)
    _session(db, user, book, noon - timedelta(days=60), 300 * 60)  # outside 30d, inside 90d
    db.commit()

    r = client.get("/api/stats/habits?days=30&tz_offset=0").json()
    assert [b["count"] for b in r["session_lengths"]] == [1, 1, 1, 1, 1]
    assert r["sessions"] == 5 == client.get("/api/stats?days=30&tz_offset=0").json()["headline"]["total_sessions"]
    assert r["longest_session"]["seconds"] == 150 * 60
    assert r["longest_session"]["title"] == "Habit Book"
    days = r["last_14_days"]
    assert len(days) == 14
    by_date = {d["date"]: d for d in days}
    yesterday = (noon - timedelta(days=1) - timedelta(hours=4)).date().isoformat()
    assert by_date[yesterday]["seconds"] == 600
    assert [s["seconds"] for s in by_date[yesterday]["sittings"]] == [600]

    r90 = client.get("/api/stats/habits?days=90&tz_offset=0").json()
    assert r90["longest_session"]["seconds"] == 300 * 60
    assert r90["session_lengths"][-1]["count"] == 2


def test_library_published_and_time_to_finish(client, db, admin_user, make_book):
    user, _ = admin_user
    quick = make_book(title="Quick", year=2019)
    bulk = make_book(title="Bulk Marked", year=2021)
    no_year = make_book(title="No Year")
    start = NOW - timedelta(days=40)
    # quick: read on 3 consecutive days, finished the third day
    for i in range(3):
        _session(db, user, quick, start + timedelta(days=i, hours=12), 600)
    # bulk: read on days 0 and 4, marked read 30 days later
    _session(db, user, bulk, start + timedelta(hours=12), 600)
    _session(db, user, bulk, start + timedelta(days=4, hours=12), 600)
    for b, fin in ((quick, start + timedelta(days=2, hours=13)), (bulk, start + timedelta(days=34)), (no_year, NOW)):
        db.add(UserBookStatus(user_id=user.id, book_id=b.id, status="read", finished_at=fin, updated_at=fin))
    db.commit()

    r = client.get("/api/stats/library?tz_offset=0").json()
    assert r["finished"] == 3
    assert r["published"] == {"years": [{"year": 2019, "count": 1}, {"year": 2021, "count": 1}], "no_year": 1}
    ttf = r["time_to_finish"]
    assert ttf["books"] == 2                       # no_year has no reading days
    assert ttf["quickest"] == {"days": 3, "count": 1}
    assert ttf["slowest"]["days"] == 5             # stops at the last reading day, not the bulk mark
    assert ttf["slowest"]["title"] == "Bulk Marked"
    assert ttf["median_days"] == 4
    assert [b["count"] for b in ttf["buckets"]] == [0, 1, 1, 0, 0]


def test_highlight_counts(client, db, admin_user, make_book):
    user, _ = admin_user
    a, b = make_book(title="Marked A"), make_book(title="Marked B")
    for i in range(3):
        db.add(Annotation(user_id=user.id, book_id=a.id, anchor=f"/a/{i}", highlighted_text="x"))
    db.add(Annotation(user_id=user.id, book_id=b.id, anchor="/b/0", highlighted_text="y"))
    db.commit()
    r = client.get("/api/stats/highlights").json()
    assert r["count"] == 4 and r["books"] == 2
    assert r["top"][0] == {"book_id": a.id, "title": "Marked A", "count": 3}


def test_sessions_counted_filter_and_day_totals(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Covered")
    other = make_book(title="Web Only")
    t0 = NOW.replace(hour=10, minute=0, second=0) - timedelta(days=2)
    # Imported page-stats for `book`, 10:00 to 10:10 → one imported sitting (counted).
    for p in range(1, 11):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=100,
                        start_time=_epoch(t0) + (p - 1) * 60, duration_seconds=60, device="Kindle"))
    # A device session over the same sitting → not counted. A web session → counted.
    _session(db, user, book, t0, 600, device="Kindle")
    _session(db, user, other, t0 + timedelta(hours=3), 900, device="web")
    db.commit()

    q = "/api/stats/sessions?tz_offset=0&day_totals=true"
    every = client.get(q).json()
    assert every["counts"] == {"all": 3, "counted": 2, "not_counted": 1}
    assert every["total"] == 3
    counted = client.get(q + "&counted=counted").json()
    assert counted["total"] == 2 and all(s["counted"] for s in counted["sessions"])
    assert {s["kind"] for s in counted["sessions"]} == {"session", "imported"}
    not_counted = client.get(q + "&counted=not_counted").json()
    assert not_counted["total"] == 1
    assert [s["device"] for s in not_counted["sessions"]] == ["Kindle"]
    assert not not_counted["sessions"][0]["counted"]
    # Day total = the counted rows of that calendar day: imported 600 + web 900.
    assert counted["day_totals"] == {t0.date().isoformat(): 1500}


def test_one_progress_value_for_a_book(client, db, admin_user, make_book):
    """/stats and /stats/completion-estimates agree: the synced position said
    2 %, page-stats reached page 20 of 200 (10 %)."""
    user, _ = admin_user
    book = make_book(title="Two Numbers")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.02))
    for p in range(1, 21):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=200,
                        start_time=_epoch(NOW) - 3600 + p, duration_seconds=30, device="Kindle"))
    db.commit()
    stats = client.get("/api/stats?days=30").json()["books_in_progress"]
    est = client.get("/api/stats/completion-estimates").json()
    a = next(b["progress"] for b in stats if b["book_id"] == book.id)
    b = next(r["progress"] for r in est if r["book_id"] == book.id)
    assert a == b == 10.0


def test_series_next_volume_and_rated_series_size(client, db, admin_user, make_book):
    user, _ = admin_user
    v = {i: make_book(title=f"Saga {i}", series="Saga", series_index=i) for i in (1, 2, 3, 5)}
    db.add(UserBookStatus(user_id=user.id, book_id=v[1].id, status="read"))
    db.add(UserBookStatus(user_id=user.id, book_id=v[2].id, status="reading"))
    db.add(UserSeriesRating(user_id=user.id, series_name="Saga", rating=4.0))
    db.commit()
    data = client.get("/api/stats?days=30").json()
    saga = next(s for s in data["series_completion"] if s["series"] == "Saga")
    assert saga["next_index"] == 3 and saga["next_book_id"] == v[3].id

    db.add(UserBookStatus(user_id=user.id, book_id=v[3].id, status="read"))
    db.commit()
    saga = next(s for s in client.get("/api/stats?days=30").json()["series_completion"] if s["series"] == "Saga")
    assert saga["next_index"] == 4 and saga["next_book_id"] is None   # volume 4 is not in the library

    rated = next(s for s in data["ratings"]["series"] if s["series"] == "Saga")
    assert rated["book_count"] == 4
