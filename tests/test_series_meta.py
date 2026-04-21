"""Tests for series arcs and series metadata endpoints.

Endpoints under test:
    GET    /api/series/{name}/arcs
    POST   /api/arcs
    PATCH  /api/arcs/{arc_id}
    DELETE /api/arcs/{arc_id}
    POST   /api/series/{name}/arcs/bulk
    GET    /api/series/{name}/meta
    PUT    /api/series/{name}/meta
"""
import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_member(db: Session, username: str = "member_series") -> tuple[User, str]:
    """Insert a member-role user and return (user, jwt_token)."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("memberpass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id)
    db.add(perms)
    db.flush()
    token = create_access_token(subject=user.id)
    return user, token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Arc CRUD as admin ─────────────────────────────────────────────────────────

def test_create_arc_admin(client: TestClient):
    """Admin can create an arc and retrieve it."""
    resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
        "description": "The pivotal arc.",
    })
    assert resp.status_code == 201, resp.text
    arc = resp.json()
    assert arc["name"] == "Golden Age"
    assert arc["series_name"] == "Berserk"
    assert arc["start_index"] == 3.0
    assert arc["end_index"] == 21.0
    assert arc["description"] == "The pivotal arc."
    assert "id" in arc


def test_list_arcs_sorted(client: TestClient):
    """GET /api/series/{name}/arcs returns arcs sorted by start_index."""
    client.post("/api/arcs", json={"series_name": "Berserk", "name": "Black Swordsman", "start_index": 1.0, "end_index": 2.0})
    client.post("/api/arcs", json={"series_name": "Berserk", "name": "Golden Age", "start_index": 3.0, "end_index": 21.0})
    client.post("/api/arcs", json={"series_name": "Berserk", "name": "Conviction", "start_index": 22.0, "end_index": 28.0})

    resp = client.get("/api/series/Berserk/arcs")
    assert resp.status_code == 200, resp.text
    arcs = resp.json()
    assert len(arcs) == 3
    assert [a["name"] for a in arcs] == ["Black Swordsman", "Golden Age", "Conviction"]


def test_update_arc_admin(client: TestClient):
    """Admin can partially update an arc."""
    create_resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })
    arc_id = create_resp.json()["id"]

    patch_resp = client.patch(f"/api/arcs/{arc_id}", json={"description": "Updated description"})
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["description"] == "Updated description"
    # Other fields unchanged
    assert patch_resp.json()["start_index"] == 3.0


def test_delete_arc_admin(client: TestClient):
    """Admin can delete an arc; it no longer appears in the list."""
    create_resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })
    arc_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/arcs/{arc_id}")
    assert del_resp.status_code == 204, del_resp.text

    list_resp = client.get("/api/series/Berserk/arcs")
    assert all(a["id"] != arc_id for a in list_resp.json())


def test_delete_arc_not_found(client: TestClient):
    """DELETE a non-existent arc returns 404."""
    resp = client.delete("/api/arcs/999999")
    assert resp.status_code == 404


# ── Arc CRUD as member ────────────────────────────────────────────────────────

def test_member_cannot_create_arc(client: TestClient, db: Session):
    """Member receives 403 when trying to create an arc."""
    _member, member_token = _make_member(db)
    resp = client.post(
        "/api/arcs",
        json={"series_name": "Berserk", "name": "Golden Age", "start_index": 3.0, "end_index": 21.0},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403, resp.text


def test_member_cannot_update_arc(client: TestClient, db: Session):
    """Member receives 403 when trying to update an arc."""
    create_resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })
    arc_id = create_resp.json()["id"]

    _member, member_token = _make_member(db)
    resp = client.patch(
        f"/api/arcs/{arc_id}",
        json={"description": "hacked"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403, resp.text


def test_member_cannot_delete_arc(client: TestClient, db: Session):
    """Member receives 403 when trying to delete an arc."""
    create_resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })
    arc_id = create_resp.json()["id"]

    _member, member_token = _make_member(db)
    resp = client.delete(f"/api/arcs/{arc_id}", headers=_auth(member_token))
    assert resp.status_code == 403, resp.text


def test_member_can_read_arcs(client: TestClient, db: Session):
    """Member can read arcs (GET is public to authenticated users)."""
    client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })

    _member, member_token = _make_member(db)
    resp = client.get("/api/series/Berserk/arcs", headers=_auth(member_token))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ── Bulk endpoint ─────────────────────────────────────────────────────────────

def test_bulk_upsert_diff_semantics(client: TestClient):
    """Bulk endpoint: A updated, B unchanged, C deleted, D created."""
    # Seed arcs A, B, C
    client.post("/api/arcs", json={"series_name": "TestSeries", "name": "Arc A", "start_index": 1.0, "end_index": 5.0, "description": "original A"})
    client.post("/api/arcs", json={"series_name": "TestSeries", "name": "Arc B", "start_index": 6.0, "end_index": 10.0})
    client.post("/api/arcs", json={"series_name": "TestSeries", "name": "Arc C", "start_index": 11.0, "end_index": 15.0})

    # Bulk: A' (updated description), B (unchanged), D (new) — C is absent → delete
    bulk_payload = [
        {"series_name": "TestSeries", "name": "Arc A", "start_index": 1.0, "end_index": 5.0, "description": "updated A"},
        {"series_name": "TestSeries", "name": "Arc B", "start_index": 6.0, "end_index": 10.0},
        {"series_name": "TestSeries", "name": "Arc D", "start_index": 16.0, "end_index": 20.0},
    ]
    resp = client.post("/api/series/TestSeries/arcs/bulk", json=bulk_payload)
    assert resp.status_code == 200, resp.text

    result_names = [a["name"] for a in resp.json()]
    assert "Arc A" in result_names
    assert "Arc B" in result_names
    assert "Arc D" in result_names
    assert "Arc C" not in result_names, "Arc C should have been deleted"

    # Verify A was updated
    arc_a = next(a for a in resp.json() if a["name"] == "Arc A")
    assert arc_a["description"] == "updated A"


def test_bulk_upsert_empty_clears_series(client: TestClient):
    """Bulk with an empty list deletes all existing arcs for the series."""
    client.post("/api/arcs", json={"series_name": "ClearSeries", "name": "Arc X", "start_index": 1.0, "end_index": 5.0})

    resp = client.post("/api/series/ClearSeries/arcs/bulk", json=[])
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_member_cannot_bulk_upsert(client: TestClient, db: Session):
    """Member receives 403 for bulk endpoint."""
    _member, member_token = _make_member(db, username="member_bulk")
    resp = client.post(
        "/api/series/Berserk/arcs/bulk",
        json=[],
        headers=_auth(member_token),
    )
    assert resp.status_code == 403, resp.text


# ── Validation ────────────────────────────────────────────────────────────────

def test_start_gt_end_returns_400(client: TestClient):
    """start_index > end_index must return 400."""
    resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Bad Arc",
        "start_index": 10.0,
        "end_index": 5.0,
    })
    assert resp.status_code == 400, resp.text


def test_start_equals_end_is_valid(client: TestClient):
    """start_index == end_index is valid (single-volume arc)."""
    resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Single Volume",
        "start_index": 7.0,
        "end_index": 7.0,
    })
    assert resp.status_code == 201, resp.text


def test_patch_start_gt_end_returns_400(client: TestClient):
    """PATCH that makes start > end returns 400."""
    create_resp = client.post("/api/arcs", json={
        "series_name": "Berserk",
        "name": "Golden Age",
        "start_index": 3.0,
        "end_index": 21.0,
    })
    arc_id = create_resp.json()["id"]

    resp = client.patch(f"/api/arcs/{arc_id}", json={"start_index": 99.0})
    assert resp.status_code == 400, resp.text


# ── SeriesMeta ────────────────────────────────────────────────────────────────

def test_get_meta_no_row_returns_unknown(client: TestClient):
    """GET /api/series/{name}/meta returns {status: 'unknown'} when no row exists, not 404."""
    resp = client.get("/api/series/NonExistentSeries/meta")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "unknown"
    assert data["series_name"] == "NonExistentSeries"


def test_upsert_meta_creates_row(client: TestClient):
    """Admin can PUT to create a SeriesMeta row."""
    resp = client.put("/api/series/Berserk/meta", json={"status": "ongoing"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["series_name"] == "Berserk"
    assert data["status"] == "ongoing"


def test_upsert_meta_updates_existing(client: TestClient):
    """Admin can PUT again to update an existing SeriesMeta row."""
    client.put("/api/series/Berserk/meta", json={"status": "ongoing"})
    resp = client.put("/api/series/Berserk/meta", json={"status": "finished"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "finished"


def test_get_meta_after_upsert(client: TestClient):
    """GET returns the stored status after a PUT."""
    client.put("/api/series/Berserk/meta", json={"status": "hiatus"})
    resp = client.get("/api/series/Berserk/meta")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "hiatus"


def test_invalid_status_returns_400(client: TestClient):
    """PUT with an invalid status returns 422 (Pydantic validation) or 400."""
    resp = client.put("/api/series/Berserk/meta", json={"status": "abandoned"})
    # Pydantic v2 field_validator raises ValueError → FastAPI converts to 422
    assert resp.status_code in (400, 422), resp.text


def test_member_cannot_upsert_meta(client: TestClient, db: Session):
    """Member receives 403 when trying to PUT series meta."""
    _member, member_token = _make_member(db, username="member_meta")
    resp = client.put(
        "/api/series/Berserk/meta",
        json={"status": "ongoing"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403, resp.text


def test_member_can_read_meta(client: TestClient, db: Session):
    """Member can GET series meta."""
    _member, member_token = _make_member(db, username="member_meta2")
    resp = client.get("/api/series/Berserk/meta", headers=_auth(member_token))
    assert resp.status_code == 200, resp.text
