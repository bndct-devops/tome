"""The instance must never be left without a usable admin.

A single-user instance that demotes (or deactivates) its only admin would
otherwise lock itself out of every admin-gated endpoint. These tests cover the
guard in `update_user` / `delete_user`.
"""
from sqlalchemy.orm import Session

from backend.core.security import hash_password
from backend.models.user import User


def _add_admin(db: Session, username: str) -> User:
    u = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=True,
        role="admin",
        must_change_password=False,
    )
    db.add(u)
    db.flush()
    return u


def test_demoting_the_last_admin_is_refused(client, admin_user, db):
    user, _ = admin_user  # the only admin
    resp = client.put(f"/api/users/{user.id}", json={"role": "member"})
    assert resp.status_code == 400
    assert "last admin" in resp.json()["detail"].lower()

    db.refresh(user)
    assert user.is_admin is True


def test_deactivating_the_last_admin_is_refused(client, admin_user, db):
    user, _ = admin_user
    resp = client.put(f"/api/users/{user.id}", json={"is_active": False})
    assert resp.status_code == 400

    db.refresh(user)
    assert user.is_active is True


def test_deleting_via_is_admin_false_on_last_admin_is_refused(client, admin_user, db):
    user, _ = admin_user
    resp = client.put(f"/api/users/{user.id}", json={"is_admin": False})
    assert resp.status_code == 400

    db.refresh(user)
    assert user.is_admin is True


def test_demoting_an_admin_is_allowed_when_another_admin_remains(client, admin_user, db):
    user, _ = admin_user
    other = _add_admin(db, "secondadmin")

    # Demoting the *other* admin is fine — `user` is still an admin.
    resp = client.put(f"/api/users/{other.id}", json={"role": "member"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"


def test_promoting_a_member_back_to_admin_still_works(client, admin_user, db):
    # The recovery direction must never be blocked.
    member = User(
        username="lockedout",
        email="lockedout@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(member)
    db.flush()

    resp = client.put(f"/api/users/{member.id}", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


def test_an_inactive_admin_does_not_count_as_usable(client, admin_user, db):
    """A disabled admin can't log in, so it must not satisfy the guard."""
    user, _ = admin_user
    inactive = _add_admin(db, "disabledadmin")
    inactive.is_active = False
    db.flush()

    # `user` is still the only *active* admin, so demoting it must fail.
    resp = client.put(f"/api/users/{user.id}", json={"role": "member"})
    assert resp.status_code == 400
