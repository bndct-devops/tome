"""Regression tests for the book-types API.

Covers:
- POST /api/book-types — slug auto-derived from label when not supplied
- POST /api/book-types — explicit slug is accepted and used verbatim
- POST /api/book-types — duplicate slug returns 400
- PUT  /api/book-types/{id} — update succeeds without slug in body
- DELETE /api/book-types/{id} — admin can delete an unused type
"""
import pytest
from starlette.testclient import TestClient


def test_create_book_type_no_slug_derives_from_label(client: TestClient):
    """POST without a slug must succeed and derive the slug from the label."""
    resp = client.post("/api/book-types", json={
        "label": "Light Novel",
        "icon": "BookOpen",
        "color": "blue",
        "sort_order": 10,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["label"] == "Light Novel"
    assert data["slug"] == "light_novel"


def test_create_book_type_explicit_slug(client: TestClient):
    """POST with an explicit slug must use that slug (lowercased/underscored)."""
    resp = client.post("/api/book-types", json={
        "slug": "My Manga",
        "label": "Manga",
        "icon": "BookOpen",
        "color": "pink",
        "sort_order": 20,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["slug"] == "my_manga"
    assert data["label"] == "Manga"


def test_create_book_type_duplicate_slug_returns_400(client: TestClient):
    """POST with a duplicate slug must return 400."""
    client.post("/api/book-types", json={"label": "Comics", "sort_order": 1})
    resp = client.post("/api/book-types", json={"label": "Comics", "sort_order": 2})
    assert resp.status_code == 400, resp.text


def test_update_book_type_without_slug(client: TestClient):
    """PUT without a slug field must succeed (slug is Optional in BookTypeIn)."""
    create_resp = client.post("/api/book-types", json={
        "label": "Novel",
        "icon": "BookOpen",
        "color": "green",
        "sort_order": 5,
    })
    assert create_resp.status_code == 201, create_resp.text
    bt_id = create_resp.json()["id"]

    put_resp = client.put(f"/api/book-types/{bt_id}", json={
        "label": "Novel (Updated)",
        "icon": "Book",
        "color": "teal",
        "sort_order": 6,
    })
    assert put_resp.status_code == 200, put_resp.text
    data = put_resp.json()
    assert data["label"] == "Novel (Updated)"
    # Slug is NOT updated by PUT — it should still equal the original derived slug
    assert data["slug"] == "novel"


def test_delete_unused_book_type(client: TestClient):
    """Admin can delete a book type that has no associated books."""
    create_resp = client.post("/api/book-types", json={"label": "Temporary", "sort_order": 99})
    assert create_resp.status_code == 201, create_resp.text
    bt_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/book-types/{bt_id}")
    assert del_resp.status_code == 204, del_resp.text

    # Should no longer appear in list
    list_resp = client.get("/api/book-types")
    assert all(bt["id"] != bt_id for bt in list_resp.json())
