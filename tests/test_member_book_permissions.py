"""Members change only the books they uploaded and reach only the books they
may see, on the bulk, Bindery, library, export and plugin routes too."""
import pytest
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, hash_password
from backend.models.book import Book, BookFile, BookTag
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User

API_KEY = "member-permission-key"


def _user(db: Session, name: str, role: str) -> User:
    user = User(
        username=name, email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True, is_admin=False, role=role, must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def _book(db: Session, title: str, added_by: int, *, reviewed: bool = True,
          library: Library | None = None, tags: tuple[str, ...] = ()) -> Book:
    book = Book(title=title, author="Shared Author", status="active",
                added_by=added_by, is_reviewed=reviewed)
    if library is not None:
        book.libraries.append(library)
    db.add(book)
    db.flush()
    db.add(BookFile(book_id=book.id, file_path=f"/library/{book.id}/{title}.epub",
                    format="epub", file_size=10))
    for tag in tags:
        db.add(BookTag(book_id=book.id, tag=tag, source="user"))
    db.flush()
    return book


@pytest.fixture()
def w(db: Session, admin_user, client):
    admin, admin_token = admin_user
    member = _user(db, "mina", "member")
    guest = _user(db, "gus", "guest")
    db.add(ApiKey(user_id=member.id, key_hash=ApiKey.hash_key(API_KEY), label="t"))
    private = Library(name="Admin only", is_public=False, owner_id=admin.id)
    own_lib = Library(name="Mina's", is_public=False, owner_id=member.id)
    db.add_all([private, own_lib])
    db.flush()

    class World:
        pass

    world = World()
    world.db, world.client = db, client
    world.admin, world.member = admin, member
    world.admin_h = {"Authorization": f"Bearer {admin_token}"}
    world.member_h = {"Authorization": f"Bearer {create_access_token(subject=member.id)}"}
    world.guest_h = {"Authorization": f"Bearer {create_access_token(subject=guest.id)}"}
    world.key_h = {"Authorization": f"Bearer {API_KEY}"}
    world.own_lib = own_lib
    world.shared = _book(db, "Shared Atlas", admin.id, tags=("keep",))
    world.hidden = _book(db, "Hidden Ledger", admin.id, library=private)
    world.mine = _book(db, "Mina's Notes", member.id)
    return world


# --- Bindery reject (S2) -----------------------------------------------------

def test_member_cannot_reject_someone_elses_book(w):
    r = w.client.delete(f"/api/bindery/reject/{w.shared.id}", headers=w.member_h)
    assert r.status_code == 403
    assert w.db.get(Book, w.shared.id) is not None


def test_reject_refuses_a_book_that_is_not_waiting_for_review(w):
    r = w.client.delete(f"/api/bindery/reject/{w.mine.id}", headers=w.member_h)
    assert r.status_code == 409
    assert w.db.get(Book, w.mine.id) is not None


def test_member_can_reject_their_own_unreviewed_book(w):
    waiting = _book(w.db, "Mina's Draft", w.member.id, reviewed=False)
    r = w.client.delete(f"/api/bindery/reject/{waiting.id}", headers=w.member_h)
    assert r.status_code in (200, 204)
    w.db.expire_all()
    assert w.db.get(Book, waiting.id) is None


def test_unreviewed_list_shows_only_visible_books(w):
    private = w.hidden.libraries[0]
    _book(w.db, "Hidden Waiting", w.admin.id, reviewed=False, library=private)
    own = _book(w.db, "Mina's Waiting", w.member.id, reviewed=False)
    r = w.client.get("/api/bindery/unreviewed", headers=w.member_h)
    assert r.status_code == 200
    assert [b["id"] for b in r.json()] == [own.id]


def test_review_all_leaves_hidden_books_alone(w):
    private = w.hidden.libraries[0]
    hidden_waiting = _book(w.db, "Hidden Waiting", w.admin.id, reviewed=False, library=private)
    own = _book(w.db, "Mina's Waiting", w.member.id, reviewed=False)
    r = w.client.put("/api/bindery/review-all", headers=w.member_h)
    assert r.status_code == 200
    w.db.expire_all()
    assert w.db.get(Book, own.id).is_reviewed is True
    assert w.db.get(Book, hidden_waiting.id).is_reviewed is False


# --- adding to your own library (S3) -----------------------------------------

def test_adding_a_hidden_book_to_your_library_does_not_reveal_it(w):
    r = w.client.post(f"/api/libraries/{w.own_lib.id}/books",
                      json={"book_ids": [w.hidden.id, w.shared.id]}, headers=w.member_h)
    assert r.status_code == 204
    w.db.expire_all()
    assert [b.id for b in w.own_lib.books] == [w.shared.id]
    assert w.client.get(f"/api/books/{w.hidden.id}", headers=w.member_h).status_code == 404


# --- members edit only their own uploads (S4) --------------------------------

def test_bulk_metadata_skips_books_the_member_did_not_upload(w):
    r = w.client.put("/api/books/bulk-metadata", headers=w.member_h, json={
        "book_ids": [w.shared.id, w.hidden.id, w.mine.id], "author": "Rewritten", "tags": [],
    })
    assert r.status_code == 200
    assert r.json()["updated"] == 1
    assert r.json()["skipped"] == 2
    w.db.expire_all()
    assert w.db.get(Book, w.mine.id).author == "Rewritten"
    assert w.db.get(Book, w.shared.id).author == "Shared Author"
    assert [t.tag for t in w.db.get(Book, w.shared.id).tags] == ["keep"]
    assert w.db.get(Book, w.hidden.id).author == "Shared Author"


def test_admin_bulk_metadata_still_changes_every_book(w):
    r = w.client.put("/api/books/bulk-metadata", headers=w.admin_h, json={
        "book_ids": [w.shared.id, w.hidden.id, w.mine.id], "author": "Rewritten",
    })
    assert r.json()["updated"] == 3
    assert r.json()["skipped"] == 0


def test_member_cannot_apply_metadata_to_someone_elses_book(w):
    r = w.client.post(f"/api/books/{w.shared.id}/apply-metadata",
                      json={"title": "Taken Over"}, headers=w.member_h)
    assert r.status_code == 403
    w.db.expire_all()
    assert w.db.get(Book, w.shared.id).title == "Shared Atlas"


def test_member_cannot_replace_the_cover_of_someone_elses_book(w):
    r = w.client.post(
        f"/api/books/{w.shared.id}/cover", headers=w.member_h,
        files={"file": ("c.jpg", b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg")},
    )
    assert r.status_code == 403


def test_member_cannot_delete_someone_elses_book_single_or_bulk(w):
    assert w.client.delete(f"/api/books/{w.shared.id}", headers=w.member_h).status_code == 403
    w.client.post("/api/books/bulk-delete", headers=w.member_h,
                  json={"book_ids": [w.shared.id, w.hidden.id]})
    w.db.expire_all()
    assert w.db.get(Book, w.shared.id) is not None
    assert w.db.get(Book, w.hidden.id) is not None


# --- export and lookups (S6) -------------------------------------------------

@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_leaves_out_hidden_books(w, fmt):
    r = w.client.get(f"/api/books/export?format={fmt}", headers=w.guest_h)
    assert r.status_code == 200
    assert "Shared Atlas" in r.text
    assert "Hidden Ledger" not in r.text


def test_admin_export_has_everything(w):
    r = w.client.get("/api/books/export?format=json", headers=w.admin_h)
    assert "Hidden Ledger" in r.text


def test_bulk_fetch_candidates_ignores_hidden_books(w, monkeypatch):
    searched: list[str] = []

    async def fake(title, *args, **kwargs):
        searched.append(title)
        return []

    import backend.api.books as books_api
    for name in ("fetch_candidates", "_fetch_candidates"):
        if hasattr(books_api, name):
            monkeypatch.setattr(books_api, name, fake)
    r = w.client.post("/api/books/bulk-fetch-candidates", headers=w.member_h,
                      json={"book_ids": [w.hidden.id]})
    assert r.status_code == 200
    assert r.json() == []
    assert "Hidden Ledger" not in searched


# --- plugin routes (S11) -----------------------------------------------------

def test_plugin_session_for_a_hidden_book_is_refused(w):
    from backend.models.tome_sync import ReadingSession
    r = w.client.post("/api/tome-sync/session", headers=w.key_h, json={
        "book_id": w.hidden.id, "started_at": "2026-01-01T10:00:00",
        "duration_seconds": 7200,
    })
    assert r.status_code == 404
    assert w.db.query(ReadingSession).filter_by(book_id=w.hidden.id).count() == 0


def test_plugin_session_for_a_visible_book_still_works(w):
    r = w.client.post("/api/tome-sync/session", headers=w.key_h, json={
        "book_id": w.shared.id, "started_at": "2026-01-01T10:00:00",
        "duration_seconds": 600,
    })
    assert r.status_code == 201


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/tome-sync/position/{id}", None),
    ("PUT", "/api/tome-sync/position/{id}", {"progress": "x", "percentage": 0.5}),
    ("GET", "/api/tome-sync/rating/{id}", None),
    ("PUT", "/api/tome-sync/rating/{id}", {"rating": 4}),
    ("POST", "/api/tome-sync/annotations/{id}/sync", {"annotations": []}),
])
def test_plugin_book_routes_refuse_a_hidden_book(w, method, path, body):
    r = w.client.request(method, path.format(id=w.hidden.id), headers=w.key_h, json=body)
    assert r.status_code == 404
