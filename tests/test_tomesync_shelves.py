"""Shelves on the device (build 36) — /tome-sync/{shelves,shelf-books}."""
import json

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.library import SavedFilter
from backend.models.user_book_status import UserBookStatus


def _api_key(client: TestClient) -> str:
    r = client.post("/api/plugin/api-keys", json={"label": "shelf-test"})
    return r.json()["key"]


def _shelf(db: Session, owner_id: int, name: str, params: dict) -> SavedFilter:
    sf = SavedFilter(name=name, owner_id=owner_id, params=json.dumps(params))
    db.add(sf)
    db.flush()
    return sf


def test_shelves_list_with_counts(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    a = make_book(title="Deutsch Eins")
    a.language = "de"
    b = make_book(title="Deutsch Zwei")
    b.language = "deu"          # messy variant folds to the same code
    make_book(title="English One").language = "en"
    _shelf(db, user.id, "German", {"language": "de"})
    db.flush()

    key = _api_key(client)
    r = client.get("/api/tome-sync/shelves", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    shelves = {s["name"]: s for s in r.json()}
    assert shelves["German"]["book_count"] == 2


def test_shelf_books_shape_and_dedupe(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    read_book = make_book(title="Done Book", series="S", series_index=1)
    make_book(title="Not Read Book")
    db.add(UserBookStatus(user_id=user.id, book_id=read_book.id, status="read"))
    sf = _shelf(db, user.id, "Finished", {"reading_status": "read"})
    db.flush()

    key = _api_key(client)
    r = client.get(f"/api/tome-sync/shelf-books?shelf_id={sf.id}",
                   headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    data = r.json()
    assert data["shelf"] == "Finished"
    titles = [b["title"] for b in data["books"]]
    assert titles == ["Done Book"]
    # Same shape as the series drill-down (id/title/status keys present).
    assert {"id", "title", "status"} <= set(data["books"][0].keys())


def test_shelf_books_foreign_shelf_404(client: TestClient, db: Session, admin_user, make_book):
    from backend.core.security import hash_password
    from backend.models.user import User, UserPermission

    other = User(username="shelf_other", email="shelf_other@example.com",
                 hashed_password=hash_password("pass1234"), is_active=True,
                 is_admin=False, role="member", must_change_password=False)
    db.add(other)
    db.flush()
    db.add(UserPermission(user_id=other.id))
    sf = _shelf(db, other.id, "Private Shelf", {})
    db.flush()

    key = _api_key(client)  # admin's key
    r = client.get(f"/api/tome-sync/shelf-books?shelf_id={sf.id}",
                   headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404


def test_shelf_resolver_matches_books_endpoint(client: TestClient, db: Session, admin_user, make_book):
    """Drift check: the shelf resolver must return the same books as /books
    for the same filter params (the documented source of the semantics)."""
    t = make_book(title="Tagged Alpha")
    make_book(title="Untagged Beta")
    from backend.models.book import BookTag
    db.add(BookTag(book_id=t.id, tag="isekai", source="user"))
    sf = _shelf(db, admin_user[0].id, "Isekai", {"tag": "isekai"})
    db.flush()

    key = _api_key(client)
    device = client.get(f"/api/tome-sync/shelf-books?shelf_id={sf.id}",
                        headers={"Authorization": f"Bearer {key}"}).json()
    web = client.get("/api/books?tag=isekai").json()
    assert sorted(b["id"] for b in device["books"]) == sorted(b["id"] for b in web)


def test_unsupported_params_reported_not_fatal(client: TestClient, db: Session, admin_user, make_book):
    make_book(title="Any Book")
    sf = _shelf(db, admin_user[0].id, "Weird", {"missing": "cover", "sort": "rating"})
    db.flush()
    key = _api_key(client)
    data = client.get(f"/api/tome-sync/shelf-books?shelf_id={sf.id}",
                      headers={"Authorization": f"Bearer {key}"}).json()
    assert "missing" in data["unsupported_filters"]
    assert len(data["books"]) >= 1  # supported subset (none) still lists visible books
