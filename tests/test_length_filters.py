"""Length sort and word-count bounds on the library, shelves and backlog."""
import json

import pytest

from backend.api.stats import _backlog_books
from backend.models.library import SavedFilter
from backend.services.shelf_resolver import shelf_query


@pytest.fixture
def lengths(make_book, db):
    books = []
    for title, words in [("Short", 20000), ("Long", 120000), ("Medium A", 50000),
                         ("Medium B", 50000), ("Unknown", None), ("Zero", 0)]:
        book = make_book(title=title)
        book.word_count = words
        books.append(book)
    db.flush()
    return books


def _titles(response):
    assert response.status_code == 200
    return [b["title"] for b in response.json()]


@pytest.mark.parametrize("order,expected", [
    ("asc", ["Short", "Medium A", "Medium B", "Long", "Unknown", "Zero"]),
    ("desc", ["Long", "Medium A", "Medium B", "Short", "Unknown", "Zero"]),
])
def test_sort_puts_unknown_last_and_pages_stably(client, lengths, order, expected):
    titles = []
    for skip in (0, 2, 4):
        titles += _titles(client.get("/api/books", params={
            "sort": "word_count", "order": order, "skip": skip, "limit": 2}))
    assert titles == expected


@pytest.mark.parametrize("bounds,expected", [
    ({"max_words": 50000}, ["Short", "Medium A", "Medium B"]),
    ({"min_words": 50000, "max_words": 50000}, ["Medium A", "Medium B"]),
    ({"min_words": 0}, ["Short", "Medium A", "Medium B", "Long"]),
    ({"min_words": 60000, "max_words": 50000}, []),
])
def test_bounds_match_list_and_ids(client, lengths, bounds, expected):
    response = client.get("/api/books", params={**bounds, "sort": "word_count"})
    assert _titles(response) == expected
    ids = client.get("/api/books/ids", params=bounds).json()["ids"]
    assert set(ids) == {b["id"] for b in response.json()}


def test_negative_bound_is_rejected(client):
    assert client.get("/api/books", params={"min_words": -1}).status_code == 422


def test_length_sort_applies_inside_a_series(client, make_book, db):
    first = make_book(title="First", series="Saga", series_index=1)
    second = make_book(title="Second", series="Saga", series_index=2)
    first.word_count, second.word_count = 100000, 20000
    db.flush()
    assert _titles(client.get("/api/books", params={"series": "Saga", "sort": "word_count"})) == ["Second", "First"]
    assert _titles(client.get("/api/books", params={"series": "Saga", "sort": "title", "order": "desc"})) == ["First", "Second"]


@pytest.mark.parametrize("params,expected", [
    ({"min_words": "50000", "max_words": "50000"}, {"Medium A", "Medium B"}),
    ({"min_words": "abc"}, set()),
    ({"max_words": "-1"}, set()),
])
def test_saved_shelf_bounds(lengths, db, admin_user, params, expected):
    user = admin_user[0]
    query, unsupported = shelf_query(db, user, params)
    assert unsupported == []
    assert {b.title for b in query.all()} == expected
    shelf = SavedFilter(name="Length shelf", owner_id=user.id, params=json.dumps(params))
    db.add(shelf)
    db.flush()
    assert {b.title for b in _backlog_books(db, user, f"shelf:{shelf.id}")} == expected
