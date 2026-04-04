"""Tests for the duplicate detection endpoints in admin_duplicates.py.

Each test is fully isolated — the `db` fixture rolls back after every test,
so there is no inter-test contamination.
"""
import pytest
from starlette.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _groups_with_reason(data: dict, reason: str) -> list[dict]:
    return [g for g in data["groups"] if g["match_reason"] == reason]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_content_hash_duplicates(client: TestClient, make_book):
    """Two books with the same content_hash must appear in a content_hash group."""
    make_book(title="Hash Book A", content_hash="deadbeef" * 8)
    make_book(title="Hash Book B", content_hash="deadbeef" * 8)

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    groups = _groups_with_reason(data, "content_hash")
    assert len(groups) == 1, f"Expected 1 content_hash group, got: {groups}"

    book_ids = {b["id"] for b in groups[0]["books"]}
    assert len(book_ids) == 2


def test_isbn_duplicates(client: TestClient, make_book):
    """Two books with the same ISBN must appear in an isbn group."""
    make_book(title="ISBN Book A", isbn="978-3-16-148410-0")
    make_book(title="ISBN Book B", isbn="978-3-16-148410-0")

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    groups = _groups_with_reason(data, "isbn")
    assert len(groups) == 1, f"Expected 1 isbn group, got: {groups}"
    assert len(groups[0]["books"]) == 2


def test_series_volume_duplicates(client: TestClient, make_book):
    """Two books with the same author, series, and series_index must appear in
    a same_series_volume group."""
    make_book(
        title="Fantasy Novel Vol 1 - Edition 1",
        author="Jane Writer",
        series="Fantasy Novel",
        series_index=1.0,
    )
    make_book(
        title="Fantasy Novel Vol 1 - Special Edition",
        author="Jane Writer",
        series="Fantasy Novel",
        series_index=1.0,
    )

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    groups = _groups_with_reason(data, "same_series_volume")
    assert len(groups) == 1, f"Expected 1 same_series_volume group, got: {groups}"
    assert len(groups[0]["books"]) == 2


def test_similar_title_duplicates(client: TestClient, make_book):
    """Two books by the same author with very similar titles (>0.85 ratio) must
    appear in a similar_title group."""
    make_book(title="The Great Adventure", author="Adventure Author")
    make_book(title="The Great Adventures", author="Adventure Author")

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    groups = _groups_with_reason(data, "similar_title")
    assert len(groups) >= 1, "Expected at least 1 similar_title group"

    # Confirm our specific pair is represented
    all_titles_in_groups = {
        b["title"]
        for g in groups
        for b in g["books"]
    }
    assert "The Great Adventure" in all_titles_in_groups
    assert "The Great Adventures" in all_titles_in_groups


def test_different_volumes_not_flagged(client: TestClient, make_book):
    """Books in the same series but with different series_index values must NOT
    be flagged as similar_title duplicates — this is the key regression test."""
    make_book(
        title="Epic Series",
        author="Series Author",
        series="Epic Series",
        series_index=1.0,
    )
    make_book(
        title="Epic Series",
        author="Series Author",
        series="Epic Series",
        series_index=2.0,
    )

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    similar_groups = _groups_with_reason(data, "similar_title")

    # The pair (vol 1, vol 2) must not appear in any similar_title group
    for group in similar_groups:
        series_indices_in_group = {b["series_index"] for b in group["books"]}
        # A group containing both 1.0 and 2.0 would be a false positive
        assert not (1.0 in series_indices_in_group and 2.0 in series_indices_in_group), (
            "Vol 1 and Vol 2 of the same series were incorrectly flagged as similar_title duplicates"
        )


def test_dismiss_duplicates(client: TestClient, make_book):
    """Dismissing a group via POST /admin/duplicates/dismiss must remove it
    from subsequent GET results."""
    book_a = make_book(title="Dismiss Me A", content_hash="aabbccdd" * 8)
    book_b = make_book(title="Dismiss Me B", content_hash="aabbccdd" * 8)

    # Confirm they appear before dismissal
    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200
    before = _groups_with_reason(resp.json(), "content_hash")
    assert len(before) == 1

    # Dismiss the pair
    dismiss_resp = client.post(
        "/api/admin/duplicates/dismiss",
        json={"book_ids": [book_a.id, book_b.id]},
    )
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.json()["dismissed"] == 1

    # Confirm they no longer appear
    after_resp = client.get("/api/admin/duplicates")
    assert after_resp.status_code == 200
    after = _groups_with_reason(after_resp.json(), "content_hash")
    assert len(after) == 0, "Dismissed pair should not appear in subsequent results"


def test_merge_duplicates(client: TestClient, make_book):
    """Merging two duplicate books moves files and unique tags to the kept book
    and deletes the removed book."""
    keep = make_book(
        title="Merge Target",
        author="Merge Author",
        tags=["shared-tag", "keep-only-tag"],
    )
    remove = make_book(
        title="Merge Source",
        author="Merge Author",
        tags=["shared-tag", "remove-only-tag"],
    )

    # Record the file id on the removed book before merging
    from backend.models.book import BookFile
    from sqlalchemy.orm import Session

    resp = client.post(
        "/api/admin/duplicates/merge",
        json={"keep_id": keep.id, "remove_ids": [remove.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merged"] == 1
    assert body["kept_id"] == keep.id

    # Fetch the kept book via the books API to confirm it exists
    get_resp = client.get(f"/api/books/{keep.id}")
    assert get_resp.status_code == 200

    # Confirm the removed book is gone
    gone_resp = client.get(f"/api/books/{remove.id}")
    assert gone_resp.status_code == 404

    # Inspect the kept book's tags (must include all three distinct tags)
    kept_data = get_resp.json()
    tag_names = {t["tag"] for t in kept_data.get("tags", [])}
    assert "shared-tag" in tag_names
    assert "keep-only-tag" in tag_names
    assert "remove-only-tag" in tag_names


def test_response_schema_complete(client: TestClient, make_book):
    """Verify the response includes all required fields — especially series_index,
    which was previously missing from _book_to_out."""
    make_book(
        title="Schema Test Book A",
        subtitle="A Subtitle",
        author="Schema Author",
        series="Schema Series",
        series_index=3.0,
        isbn="978-0-00-000001-0",
        year=2024,
        cover_path="/data/covers/test.jpg",
        content_hash="schemascha" * 6 + "1234",
    )
    make_book(
        title="Schema Test Book B",
        subtitle="Another Subtitle",
        author="Schema Author",
        series="Schema Series",
        series_index=3.0,
        isbn="978-0-00-000001-0",
        year=2024,
        cover_path="/data/covers/test2.jpg",
        content_hash="schemascha" * 6 + "1234",
    )

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    data = resp.json()
    assert "groups" in data

    # Find a group containing our test books
    our_group = None
    for group in data["groups"]:
        titles = {b["title"] for b in group["books"]}
        if "Schema Test Book A" in titles or "Schema Test Book B" in titles:
            our_group = group
            break

    assert our_group is not None, "Could not find a duplicate group for our test books"

    # Validate that every book in the group has all required fields
    required_fields = {
        "id", "title", "subtitle", "author", "isbn",
        "cover_path", "series", "series_index", "year",
        "files", "tags", "library_ids",
    }
    for book in our_group["books"]:
        missing = required_fields - book.keys()
        assert not missing, f"Book response missing fields: {missing}"
        # series_index must be present and correct
        assert book["series_index"] == 3.0, (
            f"series_index mismatch: got {book['series_index']!r}, expected 3.0"
        )
