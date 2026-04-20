"""Tests for API token management (creation, listing, revocation, authentication)."""
import hashlib

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.api_token import ApiToken
from backend.models.user import User, UserPermission


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_member(db: Session, username: str = "member1") -> tuple[User, str]:
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

    perms = UserPermission(
        user_id=user.id,
        can_download=True,
        can_view_stats=True,
        can_use_opds=True,
        can_use_kosync=True,
    )
    db.add(perms)
    db.flush()

    token = create_access_token(subject=user.id)
    return user, token


# ── token creation ────────────────────────────────────────────────────────────


def test_create_token_returns_plaintext_once(client: TestClient):
    """POST /api/tokens returns plaintext token with tome_ prefix exactly once."""
    resp = client.post("/api/tokens/", json={"name": "My Token"})
    assert resp.status_code == 201
    body = resp.json()
    assert "token" in body
    assert body["token"].startswith("tome_")
    assert body["name"] == "My Token"
    assert "prefix" in body
    assert len(body["prefix"]) == 8
    # token prefix should match what's stored
    assert body["token"][5:13] == body["prefix"]


def test_create_token_db_stores_only_hash(client: TestClient, db: Session):
    """DB row should contain the sha256 hash, never the plaintext."""
    resp = client.post("/api/tokens/", json={"name": "Hash Check"})
    assert resp.status_code == 201
    body = resp.json()
    plaintext = body["token"]
    token_id = body["id"]

    db_token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    assert db_token is not None
    expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    assert db_token.token_hash == expected_hash
    # Plaintext must not appear in any DB field
    assert plaintext not in (db_token.name, db_token.prefix, db_token.token_hash)


# ── token listing ─────────────────────────────────────────────────────────────


def test_list_tokens_excludes_plaintext_and_hash(client: TestClient, db: Session):
    """GET /api/tokens/ returns prefix and username but never plaintext or hash."""
    client.post("/api/tokens/", json={"name": "Display Token"})
    resp = client.get("/api/tokens/")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert "token" not in item  # no plaintext
        assert "token_hash" not in item  # no raw hash
        assert "prefix" in item
        assert len(item["prefix"]) == 8
        assert "username" in item
        assert isinstance(item["username"], str)
        assert len(item["username"]) > 0


def test_member_can_list_own_tokens(db: Session, client: TestClient):
    """A member can list their own tokens."""
    member, member_token = _make_member(db)
    resp = client.get("/api/tokens/", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_member_all_param_returns_403(db: Session, client: TestClient):
    """Member passing ?all=true gets 403."""
    _member, member_token = _make_member(db)
    resp = client.get("/api/tokens/?all=true", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 403


def test_admin_all_param_sees_all_tokens(db: Session, client: TestClient, admin_user):
    """Admin with ?all=true sees all users' tokens."""
    member, member_token = _make_member(db)

    # Create a token as member
    client.post("/api/tokens/", json={"name": "Member Token"}, headers={"Authorization": f"Bearer {member_token}"})
    # Create a token as admin
    client.post("/api/tokens/", json={"name": "Admin Token"})

    admin, _ = admin_user
    resp = client.get("/api/tokens/?all=true")
    assert resp.status_code == 200
    items = resp.json()
    user_ids = {item["user_id"] for item in items}
    assert admin.id in user_ids
    assert member.id in user_ids
    # Every item must carry the owner's username
    for item in items:
        assert "username" in item
        assert isinstance(item["username"], str)


# ── token revocation ──────────────────────────────────────────────────────────


def test_revoked_token_returns_401(client: TestClient, db: Session):
    """A revoked API token should return 401 on subsequent authenticated requests."""
    resp = client.post("/api/tokens/", json={"name": "Revoke Me"})
    assert resp.status_code == 201
    body = resp.json()
    token_id = body["id"]
    plaintext = body["token"]

    # Revoke it
    del_resp = client.delete(f"/api/tokens/{token_id}")
    assert del_resp.status_code == 204

    # Attempt to use the revoked token
    resp = client.get("/api/books", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_member_cannot_delete_another_users_token(db: Session, client: TestClient, admin_user):
    """A member cannot revoke another user's token (403)."""
    member, member_token = _make_member(db)

    # Admin creates a token
    resp = client.post("/api/tokens/", json={"name": "Admin Token"})
    admin_token_id = resp.json()["id"]

    # Member tries to revoke admin's token
    resp = client.delete(
        f"/api/tokens/{admin_token_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403


# ── token authentication ──────────────────────────────────────────────────────


def test_api_token_authenticates_against_books_endpoint(client: TestClient):
    """A valid API token authenticates successfully against GET /api/books."""
    resp = client.post("/api/tokens/", json={"name": "Auth Test"})
    assert resp.status_code == 201
    plaintext = resp.json()["token"]

    # Use the API token (not the JWT) to call /api/books
    resp = client.get("/api/books", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200


def test_jwt_auth_still_works_after_dep_change(client: TestClient, admin_user):
    """JWT bearer authentication is unaffected by the api-token dep change."""
    _user, jwt_token = admin_user
    resp = client.get("/api/books", headers={"Authorization": f"Bearer {jwt_token}"})
    assert resp.status_code == 200


def test_invalid_api_token_returns_401(client: TestClient):
    """A fake tome_ token that's not in the DB returns 401."""
    resp = client.get("/api/books", headers={"Authorization": "Bearer tome_fakefakefakefake"})
    assert resp.status_code == 401
