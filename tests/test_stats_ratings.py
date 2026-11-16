"""Batch 1 — the Taste tab: ratings aggregations in /api/stats."""
from datetime import datetime

from backend.models.user_book_status import UserBookStatus
from backend.models.user_series_rating import UserSeriesRating


def _rate(db, user, book, rating, rated_at):
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                          rating=rating, rated_at=rated_at))


def test_ratings_block(client, db, admin_user, make_book):
    user, _ = admin_user
    great = make_book(title="Great", series="S1", series_index=1.0)
    ok = make_book(title="Ok")
    bad = make_book(title="Bad")
    _rate(db, user, great, 5, datetime(2026, 1, 1))
    _rate(db, user, ok, 3, datetime(2026, 2, 1))
    _rate(db, user, bad, 1, datetime(2026, 3, 1))
    db.add(UserSeriesRating(user_id=user.id, series_name="S1", rating=4))
    db.flush()

    r = client.get("/api/stats?days=0").json()["ratings"]
    assert r["count"] == 3
    assert r["avg"] == 3.0

    dist = {d["rating"]: d["count"] for d in r["distribution"]}
    assert dist == {1: 1, 2: 0, 3: 1, 4: 0, 5: 1}

    # books sorted by rating desc; powers top + lowest tiles
    assert [b["title"] for b in r["books"]] == ["Great", "Ok", "Bad"]
    assert r["books"][0]["rating"] == 5 and r["books"][-1]["rating"] == 1

    # by-category avg (all Uncategorized here)
    assert r["by_category"][0]["category"] == "Uncategorized"
    assert r["by_category"][0]["avg"] == 3.0 and r["by_category"][0]["count"] == 3

    # series rating, with a sample book for the cover
    assert r["series"] == [{"series": "S1", "rating": 4, "sample_book_id": great.id, "book_count": 1}]

    # trend ordered by rated_at
    assert [t["rating"] for t in r["trend"]] == [5, 3, 1]


def test_ratings_empty_user(client, db, admin_user, make_book):
    make_book(title="Unrated")
    r = client.get("/api/stats?days=0").json()["ratings"]
    assert r["count"] == 0 and r["avg"] == 0
    assert r["books"] == [] and r["series"] == [] and r["trend"] == []
    assert sum(d["count"] for d in r["distribution"]) == 0
