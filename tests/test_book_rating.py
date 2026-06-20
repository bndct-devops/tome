"""Tests for per-user book ratings/reviews (POC).

Ratings live on the same UserBookStatus row as reading status, so rating a
book you've never opened just creates an 'unread' row.
"""


def test_unrated_book_returns_null(client, make_book):
    book = make_book(title="Unrated")
    r = client.get(f"/api/books/{book.id}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["rating"] is None
    assert body["review"] is None


def test_set_rating_creates_status_row(client, make_book):
    book = make_book(title="Rate Me")
    r = client.put(f"/api/books/{book.id}/rating", json={"rating": 4})
    assert r.status_code == 200
    assert r.json()["rating"] == 4
    # Persisted and visible via the status endpoint, default status stays 'unread'
    s = client.get(f"/api/books/{book.id}/status").json()
    assert s["rating"] == 4
    assert s["status"] == "unread"


def test_rating_out_of_range_rejected(client, make_book):
    book = make_book(title="Bad Rating")
    assert client.put(f"/api/books/{book.id}/rating", json={"rating": 0}).status_code == 400
    assert client.put(f"/api/books/{book.id}/rating", json={"rating": 6}).status_code == 400


def test_clear_rating_with_null(client, make_book):
    book = make_book(title="Clear Me")
    client.put(f"/api/books/{book.id}/rating", json={"rating": 5})
    r = client.put(f"/api/books/{book.id}/rating", json={"rating": None})
    assert r.status_code == 200
    assert r.json()["rating"] is None


def test_review_independent_of_rating(client, make_book):
    book = make_book(title="Reviewed")
    # set only review — rating stays null
    r = client.put(f"/api/books/{book.id}/rating", json={"review": "Loved it."})
    assert r.status_code == 200
    assert r.json()["review"] == "Loved it."
    assert r.json()["rating"] is None
    # rating-only update leaves the review intact (partial update semantics)
    r = client.put(f"/api/books/{book.id}/rating", json={"rating": 5})
    assert r.json()["review"] == "Loved it."
    assert r.json()["rating"] == 5


def test_rating_survives_status_change(client, make_book):
    book = make_book(title="Persist")
    client.put(f"/api/books/{book.id}/rating", json={"rating": 3})
    client.put(f"/api/books/{book.id}/status", json={"status": "read"})
    s = client.get(f"/api/books/{book.id}/status").json()
    assert s["rating"] == 3
    assert s["status"] == "read"


def test_batch_statuses_include_rating(client, make_book):
    a = make_book(title="A")
    b = make_book(title="B")
    client.put(f"/api/books/{a.id}/rating", json={"rating": 4})
    r = client.post("/api/books/statuses", json={"book_ids": [a.id, b.id]})
    body = r.json()
    assert body[str(a.id)]["rating"] == 4
    # b has no status row at all → simply absent from the map
    assert str(b.id) not in body


def test_min_rating_filter(client, make_book):
    low = make_book(title="Low")
    high = make_book(title="High")
    unrated = make_book(title="Unrated")
    client.put(f"/api/books/{low.id}/rating", json={"rating": 2})
    client.put(f"/api/books/{high.id}/rating", json={"rating": 5})

    # min_rating=4 → only the 5-star book
    ids = {b["id"] for b in client.get("/api/books?min_rating=4").json()}
    assert ids == {high.id}

    # min_rating=1 → any rated book, never the unrated one
    ids = {b["id"] for b in client.get("/api/books?min_rating=1").json()}
    assert ids == {low.id, high.id}
    assert unrated.id not in ids


def test_sort_by_rating(client, make_book):
    one = make_book(title="One")
    five = make_book(title="Five")
    three = make_book(title="Three")
    client.put(f"/api/books/{one.id}/rating", json={"rating": 1})
    client.put(f"/api/books/{five.id}/rating", json={"rating": 5})
    client.put(f"/api/books/{three.id}/rating", json={"rating": 3})

    # desc — highest first; only compare the rated ones (unrated sort last/nullslast)
    order = [b["id"] for b in client.get("/api/books?sort=rating&order=desc").json()]
    rated = [bid for bid in order if bid in {one.id, three.id, five.id}]
    assert rated == [five.id, three.id, one.id]
