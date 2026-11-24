"""Drag to reorder in the sidebar: libraries and shelves keep the order sent."""
from sqlalchemy.orm import Session

from backend.core.security import hash_password, create_access_token
from backend.models.user import User
from backend.models.library import Library, SavedFilter


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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_admin_reorders_libraries(client, db: Session, admin_user):
    _admin, token = admin_user
    libs = [Library(name=n, is_public=True, owner_id=None) for n in ("Alpha", "Beta", "Gamma")]
    db.add_all(libs)
    db.flush()
    wanted = [libs[2].id, libs[0].id, libs[1].id]

    r = client.post("/api/libraries/reorder", json={"ids": wanted}, headers=_auth(token))
    assert r.status_code == 204, r.text

    got = [l["id"] for l in client.get("/api/libraries", headers=_auth(token)).json() if l["id"] in wanted]
    assert got == wanted


def test_shelves_reorder_own_only(client, db: Session):
    owner, token = _make_user(db, "shelf_owner", "member")
    other, other_token = _make_user(db, "shelf_other", "member")
    mine = [SavedFilter(name=n, owner_id=owner.id, params="{}") for n in ("One", "Two", "Three")]
    theirs = SavedFilter(name="Theirs", owner_id=other.id, params="{}", sort_order=7)
    db.add_all([*mine, theirs])
    db.flush()
    wanted = [mine[1].id, mine[2].id, mine[0].id]

    r = client.post("/api/saved-filters/reorder", json={"ids": [theirs.id, *wanted]}, headers=_auth(token))
    assert r.status_code == 204, r.text

    got = [f["id"] for f in client.get("/api/saved-filters", headers=_auth(token)).json()]
    assert got == wanted
    # Another user's shelf in the list is left alone.
    db.refresh(theirs)
    assert theirs.sort_order == 7
    assert [f["id"] for f in client.get("/api/saved-filters", headers=_auth(other_token)).json()] == [theirs.id]


def test_new_shelf_goes_last_after_a_reorder(client, db: Session):
    owner, token = _make_user(db, "shelf_new_last", "member")
    shelves = [SavedFilter(name=n, owner_id=owner.id, params="{}") for n in ("Alpha", "Mid", "Zeta")]
    db.add_all(shelves)
    db.flush()
    wanted = [shelves[2].id, shelves[0].id, shelves[1].id]
    assert client.post("/api/saved-filters/reorder", json={"ids": wanted}, headers=_auth(token)).status_code == 204

    r = client.post("/api/saved-filters", json={"name": "New", "params": {}}, headers=_auth(token))
    assert r.status_code == 201, r.text

    got = [f["name"] for f in client.get("/api/saved-filters", headers=_auth(token)).json()]
    assert got == ["Zeta", "Alpha", "Mid", "New"]


def test_new_shelf_sorts_by_name_without_a_reorder(client, db: Session):
    owner, token = _make_user(db, "shelf_by_name", "member")
    db.add_all([SavedFilter(name=n, owner_id=owner.id, params="{}") for n in ("Beta", "Delta")])
    db.flush()

    r = client.post("/api/saved-filters", json={"name": "Alpha", "params": {}}, headers=_auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 0

    got = [f["name"] for f in client.get("/api/saved-filters", headers=_auth(token)).json()]
    assert got == ["Alpha", "Beta", "Delta"]


def test_new_library_goes_last_after_a_reorder(client, db: Session, admin_user):
    _admin, token = admin_user
    libs = [Library(name=n, is_public=True, owner_id=None) for n in ("Alpha", "Mid", "Zeta")]
    db.add_all(libs)
    db.flush()
    existing = [l["id"] for l in client.get("/api/libraries", headers=_auth(token)).json()]
    wanted = [libs[2].id, libs[0].id, libs[1].id] + [i for i in existing if i not in {l.id for l in libs}]
    assert client.post("/api/libraries/reorder", json={"ids": wanted}, headers=_auth(token)).status_code == 204

    r = client.post("/api/libraries", json={"name": "New"}, headers=_auth(token))
    assert r.status_code == 201, r.text

    got = [l["id"] for l in client.get("/api/libraries", headers=_auth(token)).json()]
    assert got == [*wanted, r.json()["id"]]


def test_new_library_sorts_by_name_without_a_reorder(client, db: Session):
    member, token = _make_user(db, "lib_by_name", "member")
    db.add_all([Library(name=n, is_public=False, owner_id=member.id) for n in ("Beta", "Delta")])
    db.flush()

    r = client.post("/api/libraries", json={"name": "Alpha", "is_public": False}, headers=_auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 0

    names = [l["name"] for l in client.get("/api/libraries", headers=_auth(token)).json()]
    mine = [n for n in names if n in ("Alpha", "Beta", "Delta")]
    assert mine == ["Alpha", "Beta", "Delta"]
