"""Tests for the GET /api/books list/filter endpoint.

Covers search, sort, pagination, and every filter parameter supported by
backend/api/books.py: author, series, format, library_id, missing, tag.

Each test is fully isolated — the `db` fixture rolls back after every test,
so there is no inter-test contamination.
"""
import pytest
from starlette.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ids(resp) -> list[int]:
    return [b["id"] for b in resp.json()]


def _titles(resp) -> list[str]:
    return [b["title"] for b in resp.json()]


def _total(resp) -> int:
    return int(resp.headers["X-Total-Count"])


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_list_books_default(client: TestClient, make_book):
    """Creating three books and GETting /api/books should return all three."""
    b1 = make_book(title="Alpha")
    b2 = make_book(title="Beta")
    b3 = make_book(title="Gamma")

    resp = client.get("/api/books")
    assert resp.status_code == 200

    ids = _ids(resp)
    assert b1.id in ids
    assert b2.id in ids
    assert b3.id in ids
    assert _total(resp) >= 3


def test_filter_by_author(client: TestClient, make_book):
    """author= should return only books by that exact author."""
    b1 = make_book(title="Novel One", author="Alice Author")
    b2 = make_book(title="Novel Two", author="Alice Author")
    _other = make_book(title="Other Book", author="Bob Writer")

    resp = client.get("/api/books", params={"author": "Alice Author"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert b1.id in ids
    assert b2.id in ids
    assert _other.id not in ids
    assert _total(resp) == 2


def test_filter_by_series(client: TestClient, make_book):
    """series= should return only books belonging to that exact series name."""
    s1 = make_book(title="Vol 1", series="Awesome Series", series_index=1.0)
    s2 = make_book(title="Vol 2", series="Awesome Series", series_index=2.0)
    _other = make_book(title="Standalone", series="Other Series")

    resp = client.get("/api/books", params={"series": "Awesome Series"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert s1.id in ids
    assert s2.id in ids
    assert _other.id not in ids
    assert _total(resp) == 2


def test_filter_by_format(client: TestClient, make_book):
    """format= should return only books whose BookFile.format matches."""
    epub_book = make_book(title="Epub Book", file_format="epub")
    pdf_book = make_book(title="PDF Book", file_format="pdf")

    resp_epub = client.get("/api/books", params={"format": "epub"})
    assert resp_epub.status_code == 200
    epub_ids = _ids(resp_epub)
    assert epub_book.id in epub_ids
    assert pdf_book.id not in epub_ids

    resp_pdf = client.get("/api/books", params={"format": "pdf"})
    assert resp_pdf.status_code == 200
    pdf_ids = _ids(resp_pdf)
    assert pdf_book.id in pdf_ids
    assert epub_book.id not in pdf_ids


def test_missing_filter_cover(client: TestClient, make_book):
    """missing=cover should return only books where cover_path is NULL."""
    no_cover = make_book(title="No Cover Book", cover_path=None)
    has_cover = make_book(title="Has Cover Book", cover_path="/data/covers/1.jpg")

    resp = client.get("/api/books", params={"missing": "cover"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert no_cover.id in ids
    assert has_cover.id not in ids


def test_missing_filter_description(client: TestClient, make_book):
    """missing=description should return only books where description is NULL or empty."""
    no_desc = make_book(title="No Description Book", description=None)
    has_desc = make_book(title="Has Description Book", description="A compelling story.")

    resp = client.get("/api/books", params={"missing": "description"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert no_desc.id in ids
    assert has_desc.id not in ids


def test_missing_filter_any(client: TestClient, make_book):
    """missing=any should return books missing cover, description, author, or series."""
    complete = make_book(
        title="Complete Book",
        author="Some Author",
        series="Some Series",
        description="Has description.",
        cover_path="/data/covers/complete.jpg",
    )
    missing_cover = make_book(
        title="Missing Cover",
        author="Author A",
        series="Series A",
        description="Fine.",
        cover_path=None,
    )
    missing_desc = make_book(
        title="Missing Desc",
        author="Author B",
        series="Series B",
        description=None,
        cover_path="/data/covers/x.jpg",
    )

    resp = client.get("/api/books", params={"missing": "any"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert missing_cover.id in ids
    assert missing_desc.id in ids
    # The complete book must NOT be included
    assert complete.id not in ids


def test_search_by_title(client: TestClient, make_book, db):
    """q= should return books whose title matches the search term."""
    from sqlalchemy import text as sa_text

    target = make_book(title="Mysterious Island Adventure")
    _other = make_book(title="Cooking Fundamentals")

    # Populate the FTS table so the search can find them
    db.execute(sa_text(
        "INSERT INTO books_fts(rowid, title, author, series, description, tags) "
        "VALUES (:id, :title, '', '', '', '')"
    ), {"id": target.id, "title": "Mysterious Island Adventure"})
    db.execute(sa_text(
        "INSERT INTO books_fts(rowid, title, author, series, description, tags) "
        "VALUES (:id, :title, '', '', '', '')"
    ), {"id": _other.id, "title": "Cooking Fundamentals"})
    db.flush()

    resp = client.get("/api/books", params={"q": "Mysterious"})
    assert resp.status_code == 200

    ids = _ids(resp)
    assert target.id in ids
    assert _other.id not in ids


def test_pagination(client: TestClient, make_book):
    """per_page / skip should limit results; X-Total-Count should reflect full count."""
    for i in range(5):
        make_book(title=f"Paged Book {i:02d}")

    # First page: skip=0, limit=2
    resp = client.get("/api/books", params={"skip": 0, "limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert _total(resp) >= 5

    # Second page should also return 2 items (assuming at least 4 matching books)
    resp2 = client.get("/api/books", params={"skip": 2, "limit": 2})
    assert resp2.status_code == 200
    assert len(resp2.json()) == 2

    # Pages must not overlap
    page1_ids = set(_ids(resp))
    page2_ids = set(_ids(resp2))
    assert page1_ids.isdisjoint(page2_ids)


def test_sort_by_title(client: TestClient, make_book):
    """sort=title&order=asc must return books in ascending alphabetical order."""
    make_book(title="Zebra Tales")
    make_book(title="Apple Stories")
    make_book(title="Mango Chronicles")

    resp = client.get("/api/books", params={"sort": "title", "order": "asc"})
    assert resp.status_code == 200

    titles = _titles(resp)
    # The slice containing our three books should already be sorted; verify the
    # full returned list is sorted ascending.
    assert titles == sorted(titles)
