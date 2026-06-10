"""Regression tests for library visibility & sharing (issue #31).

Before the fix, GET /api/libraries only returned libraries the caller *owned*
or global ones (owner_id IS NULL). A public library created by another user, or
a private library shared with the caller, was never returned — so the
``is_public`` flag and individual sharing did nothing.
"""
from sqlalchemy.orm import Session

from backend.core.security import hash_password, create_access_token
from backend.models.user import User
from backend.models.library import Library


def _make_user(db: Session, username: str, role: str) -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=(role == "admin"),
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user, create_access_token(subject=user.id)


def _lib_ids(client, token: str) -> set[int]:
    r = client.get("/api/libraries", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return {lib["id"] for lib in r.json()}


def _libs(client, token: str) -> list[dict]:
    r = client.get("/api/libraries", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_public_library_visible_to_other_user(client, db: Session):
    """A public library owned by user A is visible to an unrelated user B."""
    user_a, token_a = _make_user(db, "owner_a", "member")
    _user_b, token_b = _make_user(db, "viewer_b", "member")

    pub = Library(name="Shared Public", is_public=True, owner_id=user_a.id)
    priv = Library(name="A's Private", is_public=False, owner_id=user_a.id)
    db.add_all([pub, priv])
    db.flush()

    visible = _lib_ids(client, token_b)
    assert pub.id in visible           # public → shared with everyone
    assert priv.id not in visible      # private & unshared → hidden

    # can_edit: owner may edit, an unrelated viewer may not (so the UI hides
    # the edit/delete/add controls instead of letting them 403).
    b_pub = next(l for l in _libs(client, token_b) if l["id"] == pub.id)
    assert b_pub["can_edit"] is False
    a_pub = next(l for l in _libs(client, token_a) if l["id"] == pub.id)
    assert a_pub["can_edit"] is True


def test_admin_can_edit_everything(client, db: Session, admin_user):
    """Admins get can_edit=True for any library, including private/global ones."""
    _admin, admin_token = admin_user
    member, _ = _make_user(db, "owner_m", "member")
    priv = Library(name="Member Private", is_public=False, owner_id=member.id)
    glob = Library(name="Global Lib", is_public=True, owner_id=None)
    db.add_all([priv, glob])
    db.flush()

    libs = {l["id"]: l for l in _libs(client, admin_token)}
    assert libs[priv.id]["can_edit"] is True
    assert libs[glob.id]["can_edit"] is True

    # A member never gets can_edit on a global library (admin-only mutation)
    _m2, m2_token = _make_user(db, "viewer_m2", "member")
    m2_libs = {l["id"]: l for l in _libs(client, m2_token)}
    assert m2_libs[glob.id]["can_edit"] is False


def test_private_library_visible_after_sharing(client, db: Session):
    """A private library becomes visible to user B once the owner assigns them."""
    user_a, token_a = _make_user(db, "owner_a2", "member")
    user_b, token_b = _make_user(db, "viewer_b2", "member")

    priv = Library(name="Shared Private", is_public=False, owner_id=user_a.id)
    db.add(priv)
    db.flush()

    assert priv.id not in _lib_ids(client, token_b)

    # Owner (not admin) shares it with user B
    r = client.post(
        f"/api/libraries/{priv.id}/users",
        json={"user_id": user_b.id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 204, r.text

    assert priv.id in _lib_ids(client, token_b)

    # Un-sharing hides it again
    r = client.delete(
        f"/api/libraries/{priv.id}/users/{user_b.id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 204, r.text
    assert priv.id not in _lib_ids(client, token_b)


def test_owner_cannot_share_another_users_library(client, db: Session):
    """A member may only share libraries they own (not someone else's)."""
    user_a, _ = _make_user(db, "owner_a3", "member")
    user_b, token_b = _make_user(db, "stranger_b3", "member")

    priv = Library(name="A's Private 3", is_public=False, owner_id=user_a.id)
    db.add(priv)
    db.flush()

    r = client.post(
        f"/api/libraries/{priv.id}/users",
        json={"user_id": user_b.id},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 403, r.text


def test_member_can_list_users_for_sharing(client, db: Session):
    """Members need /users/list to populate the share picker."""
    _member, token = _make_user(db, "member_share", "member")
    r = client.get("/api/users/list", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
