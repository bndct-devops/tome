"""Reading Calendar endpoints: GET /api/stats/calendar and /stats/calendar/day."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.book import BookChapter
from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession

# Reading days are local + 4h rollover; tz_offset=0 keeps UTC == local here.
D = datetime(2026, 5, 12, 18, 0)  # a Tuesday evening


def _session(db, user_id, book_id, start, secs=1200, pages=30, p0=None, p1=None, device="web"):
    db.add(ReadingSession(user_id=user_id, book_id=book_id, started_at=start,
                          ended_at=start + timedelta(seconds=secs), duration_seconds=secs,
                          pages_turned=pages, progress_start=p0, progress_end=p1, device=device))
    db.flush()


def _month(client, month="2026-05"):
    r = client.get(f"/api/stats/calendar?month={month}&tz_offset=0")
    assert r.status_code == 200, r.text
    return r.json()


def _day(client, day):
    r = client.get(f"/api/stats/calendar/day?day={day}&tz_offset=0")
    assert r.status_code == 200, r.text
    return r.json()


def test_month_days_and_totals(client: TestClient, make_book, admin_user, db: Session):
    user, _ = admin_user
    a, b = make_book(title="Alpha"), make_book(title="Beta")
    _session(db, user.id, a.id, D, secs=1800)
    _session(db, user.id, b.id, D + timedelta(hours=1), secs=600)
    _session(db, user.id, a.id, D + timedelta(days=1), secs=900)
    # 02:00 on the 14th belongs to the 13th's reading day (4h rollover).
    _session(db, user.id, a.id, datetime(2026, 5, 14, 2, 0), secs=300)

    data = _month(client)
    by_day = {d["date"]: d for d in data["days"]}
    assert by_day["2026-05-12"]["seconds"] == 2400
    assert [x["title"] for x in by_day["2026-05-12"]["books"]] == ["Alpha", "Beta"]  # longest first
    assert by_day["2026-05-13"]["seconds"] == 1200
    assert "2026-05-14" not in by_day
    assert data["totals"]["seconds"] == 3600
    assert data["totals"]["reading_days"] == 2
    assert data["totals"]["best_day"] == "2026-05-12"
    assert data["first_day"] == "2026-05-12"


def test_month_rejects_bad_month(client: TestClient):
    assert client.get("/api/stats/calendar?month=2026-13").status_code == 422
    assert client.get("/api/stats/calendar?month=May").status_code == 422


def test_finished_only_the_first_time_the_end_is_reached(client: TestClient, make_book, admin_user, db: Session):
    user, _ = admin_user
    book = make_book(title="Gamma")
    _session(db, user.id, book.id, D, p0=0.5, p1=1.0)
    # A later peek at the last pages is not a second finish.
    _session(db, user.id, book.id, D + timedelta(days=3), p0=0.98, p1=1.0)

    data = _month(client)
    finished = {d["date"]: [b["title"] for b in d["finished"]] for d in data["days"] if d["finished"]}
    assert finished == {"2026-05-12": ["Gamma"]}
    assert data["totals"]["books_finished"] == 1


def test_day_detail(client: TestClient, make_book, admin_user, db: Session):
    user, _ = admin_user
    book = make_book(title="Delta")
    db.add_all([
        BookChapter(book_id=book.id, idx=0, title="Chapter 1", start_fraction=0.0, end_fraction=0.5),
        BookChapter(book_id=book.id, idx=1, title="Chapter 2", start_fraction=0.5, end_fraction=0.8),
        BookChapter(book_id=book.id, idx=2, title="Chapter 3", start_fraction=0.8, end_fraction=1.0),
    ])
    # Two sessions 10 minutes apart = one sitting; a third after a long gap = another.
    _session(db, user.id, book.id, D, secs=600, pages=20, p0=0.0, p1=0.3)
    _session(db, user.id, book.id, D + timedelta(minutes=20), secs=600, pages=20, p0=0.3, p1=0.55)
    _session(db, user.id, book.id, D + timedelta(hours=4), secs=1200, pages=40, p0=0.55, p1=0.6)
    _session(db, user.id, book.id, D + timedelta(days=1), secs=300, pages=5, p0=0.6, p1=0.62)

    day = _day(client, "2026-05-12")
    assert day["seconds"] == 2400 and day["pages"] == 80
    (b,) = day["books"]
    assert b["title"] == "Delta"
    assert b["chapters"] == ["Chapter 1", "Chapter 2"]
    assert (b["progress_from"], b["progress_to"]) == (0.0, 0.6)
    assert b["started"] is True and b["finished"] is False
    assert b["sittings"] == 2 and len(day["sittings"]) == 2
    assert day["is_month_best"] is True
    assert day["streak"] == {"day": 1, "length": 2, "start": "2026-05-12", "end": "2026-05-13", "is_best": True}

    next_day = _day(client, "2026-05-13")
    assert next_day["books"][0]["started"] is False
    assert next_day["is_month_best"] is False
    assert next_day["streak"]["day"] == 2


def test_day_reconciles_page_stats(client: TestClient, make_book, admin_user, db: Session):
    """A device session that imported page-stats describe is not counted twice."""
    user, _ = admin_user
    book = make_book(title="Epsilon")
    start = int(D.timestamp()) if D.tzinfo else int((D - datetime(1970, 1, 1)).total_seconds())
    for i in range(10):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=i + 1, total_pages=100,
                        start_time=start + i * 60, duration_seconds=60, device="kindle"))
    _session(db, user.id, book.id, D, secs=600, pages=10, device="kindle")  # same sitting, live-recorded

    day = _day(client, "2026-05-12")
    assert day["seconds"] == 600
    assert day["books"][0]["pages"] == 10


def test_empty_day(client: TestClient):
    day = _day(client, "2026-05-01")
    assert day["books"] == [] and day["seconds"] == 0 and day["streak"] is None
    assert client.get("/api/stats/calendar/day?day=nope").status_code == 422


def test_no_biggest_day_on_a_tie(client: TestClient, make_book, admin_user, db: Session):
    """Equal days: none of them is "the biggest day this month"."""
    user, _ = admin_user
    book = make_book(title="Zeta")
    for i in range(3):
        _session(db, user.id, book.id, D + timedelta(days=i), secs=1500)
    assert _month(client)["totals"]["best_day"] is None
    assert _day(client, "2026-05-13")["is_month_best"] is False
    _session(db, user.id, book.id, D + timedelta(days=1, hours=2), secs=60)
    assert _month(client)["totals"]["best_day"] == "2026-05-13"
    assert _day(client, "2026-05-13")["is_month_best"] is True


def test_shade_scale_is_personal_not_monthly(client: TestClient, make_book, admin_user, db: Session):
    user, _ = admin_user
    book = make_book(title="Eta")
    _session(db, user.id, book.id, D, secs=600)
    assert _month(client)["scale_seconds"] == 1800  # 30-minute floor
