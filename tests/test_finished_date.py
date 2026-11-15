"""Books finished count by finished_at, not by updated_at.

updated_at moves on every rating, review or position write, so a book
finished long ago whose status row was touched today must not count as
finished today. Legacy rows without finished_at fall back to updated_at.
"""
from datetime import datetime, timedelta

from backend.models.user_book_status import UserBookStatus


def _status(db, user, book, finished_at, updated_at):
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                         finished_at=finished_at)
    db.add(row)
    db.flush()
    row.updated_at = updated_at
    db.flush()
    return row


def _setup(db, user, make_book):
    now = datetime.utcnow()
    # Finished 400 days ago, status row touched today (for example a rating).
    old = make_book(title="The Quiet Ledger")
    old.word_count = 50_000
    # Finished 60 days ago, touched today.
    mid = make_book(title="Salt of the Lowlands")
    mid.word_count = 70_000
    db.flush()
    # Noon so the 4 h reading-day rollover never moves the month.
    _status(db, user, old, (now - timedelta(days=400)).replace(hour=12), now)
    _status(db, user, mid, (now - timedelta(days=60)).replace(hour=12), now)
    return now, old, mid


def test_stats_counts_by_finish_date(client, db, admin_user, make_book):
    user, _ = admin_user
    now, old, mid = _setup(db, user, make_book)

    s = client.get("/api/stats?days=30").json()
    assert s["headline"]["books_finished"] == 0
    assert s["books_finished"] == []

    s = client.get("/api/stats?days=0").json()
    dates = {b["title"]: b["date"] for b in s["books_finished"]}
    assert dates["The Quiet Ledger"] == (now - timedelta(days=400)).date().isoformat()
    assert dates["Salt of the Lowlands"] == (now - timedelta(days=60)).date().isoformat()

    months = {m["month"]: m["books_finished"] for m in s["monthly_comparison"]}
    assert months[now.strftime("%Y-%m")] == 0
    assert months[(now - timedelta(days=60)).strftime("%Y-%m")] == 1

    by_year = {y["year"]: y["words"] for y in s["words"]["by_year"]}
    old_year = (now - timedelta(days=400)).year
    mid_year = (now - timedelta(days=60)).year
    expected: dict[int, int] = {}
    expected[old_year] = expected.get(old_year, 0) + 50_000
    expected[mid_year] = expected.get(mid_year, 0) + 70_000
    assert by_year == expected


def test_home_counts_by_finish_date(client, db, admin_user, make_book):
    user, _ = admin_user
    _setup(db, user, make_book)
    assert client.get("/api/home/stats").json()["books_finished_30d"] == 0


def test_goal_counts_by_finish_date(client, db, admin_user, make_book):
    user, _ = admin_user
    _setup(db, user, make_book)
    r = client.post("/api/goals", json={"kind": "books_per_month", "target": 5})
    assert r.status_code == 201, r.text
    assert r.json()["current"] == 0


def test_legacy_row_without_finished_at_uses_updated_at(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Lanterns at Low Tide")
    db.flush()
    _status(db, user, book, None, datetime.utcnow() - timedelta(days=3))
    assert client.get("/api/stats?days=30").json()["headline"]["books_finished"] == 1
    assert client.get("/api/home/stats").json()["books_finished_30d"] == 1
    r = client.post("/api/goals", json={"kind": "books_per_month", "target": 5})
    # 3 days ago may fall in the previous month near a month start.
    expected = 1 if (datetime.utcnow() - timedelta(days=3)).month == datetime.utcnow().month else 0
    assert r.json()["current"] == expected
