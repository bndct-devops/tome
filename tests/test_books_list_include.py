"""GET /api/books?include=description — opt-in description on list items.

The default reply must stay exactly as it was (the web UI, OPDS and other
clients read it); include=description adds one field per book and no queries.
"""
import json

from sqlalchemy import event
from starlette.testclient import TestClient

from tests.conftest import test_engine


def _count_queries(fn) -> int:
    seen: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(test_engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(test_engine, "before_cursor_execute", _before)
    return len(seen)


def test_default_reply_has_no_description(client: TestClient, make_book):
    make_book(title="Alpha", description="A long blurb")

    resp = client.get("/api/books")
    assert resp.status_code == 200
    assert all("description" not in b for b in resp.json())


def test_include_description_adds_the_field(client: TestClient, make_book):
    a = make_book(title="Alpha", description="A long blurb, with ünïcode")
    b = make_book(title="Beta", description=None)

    resp = client.get("/api/books", params={"include": "description"})
    assert resp.status_code == 200
    by_id = {row["id"]: row for row in resp.json()}
    assert by_id[a.id]["description"] == "A long blurb, with ünïcode"
    assert "description" in by_id[b.id] and by_id[b.id]["description"] is None
    assert resp.headers["content-type"] == "application/json"


def test_include_description_is_the_default_reply_plus_one_field(client: TestClient, make_book):
    make_book(title="Alpha", description="One", series="S", series_index=1, tags=["x"])
    make_book(title="Beta", description="Two", series="S", series_index=2)
    make_book(title="Gamma", description=None)

    plain = client.get("/api/books", params={"sort": "added_at", "order": "desc"})
    extra = client.get("/api/books", params={"sort": "added_at", "order": "desc", "include": "description"})

    stripped = [{k: v for k, v in row.items() if k != "description"} for row in extra.json()]
    assert stripped == plain.json()
    # Same key order and same encoding as the default reply, field appended.
    assert list(extra.json()[0].keys())[:-1] == list(plain.json()[0].keys())
    assert list(extra.json()[0].keys())[-1] == "description"


def test_headers_carry_over(client: TestClient, make_book):
    for i in range(5):
        make_book(title=f"Book {i}", description=f"d{i}")

    plain = client.get("/api/books", params={"limit": 2})
    extra = client.get("/api/books", params={"limit": 2, "include": "description"})

    assert extra.headers["X-Total-Count"] == plain.headers["X-Total-Count"] == "5"
    assert extra.headers["Access-Control-Expose-Headers"] == "X-Total-Count"
    assert len(extra.json()) == 2


def test_unknown_include_leaves_the_reply_byte_identical(client: TestClient, make_book):
    make_book(title="Alpha", description="One")
    make_book(title="Beta", description="Two")

    plain = client.get("/api/books")
    other = client.get("/api/books", params={"include": "nonsense"})
    empty = client.get("/api/books", params={"include": ""})

    assert other.content == plain.content
    assert empty.content == plain.content


def test_include_list_with_spaces(client: TestClient, make_book):
    make_book(title="Alpha", description="One")

    resp = client.get("/api/books", params={"include": "nonsense, description"})
    assert resp.json()[0]["description"] == "One"


def test_include_description_with_group_by_series(client: TestClient, make_book):
    make_book(title="Vol 1", series="Saga", series_index=1, description="First")
    make_book(title="Vol 2", series="Saga", series_index=2, description="Second")

    resp = client.get("/api/books", params={"group_by_series": True, "include": "description"})
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["series_count"] == 2
    assert rows[0]["description"] == "First"


def test_include_description_adds_no_queries(client: TestClient, make_book):
    for i in range(20):
        make_book(title=f"Book {i}", description=f"Blurb {i}", tags=[f"t{i}"])

    # Warm-up so first-request work (lazy imports, caches) is not counted.
    client.get("/api/books")
    client.get("/api/books", params={"include": "description"})

    plain = _count_queries(lambda: client.get("/api/books"))
    extra = _count_queries(lambda: client.get("/api/books", params={"include": "description"}))
    assert extra == plain


def test_include_description_matches_book_detail(client: TestClient, make_book):
    book = make_book(title="Alpha", description="<p>Some <b>html</b></p>")

    row = client.get("/api/books", params={"include": "description"}).json()[0]
    detail = client.get(f"/api/books/{book.id}").json()
    assert row["description"] == detail["description"]
    assert json.dumps(row["updated_at"]) == json.dumps(detail["updated_at"])
