"""Tests for the authentication flow (login, /me, token validation, admin guard)."""
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_non_admin(db: Session) -> tuple[User, str]:
    """Insert a plain (non-admin) user and return (user, jwt_token)."""
    user = User(
        username="regularuser",
        email="regular@example.com",
        hashed_password=hash_password("userpass123"),
        is_active=True,
        is_admin=False,
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


# ── login ─────────────────────────────────────────────────────────────────────


def test_login_success(client: TestClient):
    """Valid credentials return a bearer token."""
    # The admin_user fixture uses username="testadmin" / password="adminpass123"
    resp = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "adminpass123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 0


def test_login_wrong_password(client: TestClient):
    """Wrong password returns 401."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "wrongpassword"},
    )
    assert resp.status_code == 401


def test_login_nonexistent_user(client: TestClient):
    """Login with a username that doesn't exist returns 401."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "doesntmatter"},
    )
    assert resp.status_code == 401


# ── /me ───────────────────────────────────────────────────────────────────────


def test_me_with_valid_token(client: TestClient, admin_user):
    """Authenticated GET /api/auth/me returns the current user's info."""
    user, _token = admin_user
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == user.username
    assert body["email"] == user.email
    assert body["is_admin"] is True


def test_me_without_token(client: TestClient):
    """GET /api/auth/me with no Authorization header returns 401."""
    resp = client.get("/api/auth/me", headers={"Authorization": ""})
    assert resp.status_code == 401


def test_me_with_invalid_token(client: TestClient):
    """GET /api/auth/me with a garbage token returns 401."""
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer this.is.not.a.real.token"},
    )
    assert resp.status_code == 401


# ── admin-only endpoint ───────────────────────────────────────────────────────


def test_admin_endpoint_as_admin(client: TestClient):
    """Admin user can access GET /api/admin/duplicates (200)."""
    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200


def test_admin_endpoint_as_non_admin(db: Session, client: TestClient):
    """Non-admin user receives 403 when accessing an admin-only endpoint."""
    _user, token = _make_non_admin(db)

    resp = client.get(
        "/api/admin/duplicates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
