"""Deleting a user who has uploads, libraries and personal data.

Before the fix, books.added_by / libraries.owner_id / saved_filters.owner_id
had no delete rule, so DELETE /api/users/{id} failed with a foreign key error
(HTTP 500) while the audit log already said ``users.deleted``.

The rule now: the user's personal data goes (shelves, tokens, statuses), their
uploads and libraries stay and pass to the admin who deleted them, and an
upload that was in no library (private to the uploader) is filed into a new
private library owned by that admin, so it does not become shared.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, hash_password
from backend.models.api_token import ApiToken
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookFile
from backend.models.library import Library, SavedFilter
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus


def _make_user(db: Session, username: str, role: str = "member") -> User:
    u = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=False,
        role=role,
        must_change_password=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_book(db: Session, title: str, added_by: int | None) -> Book:
    book = Book(title=title, author="Invented Author", status="active", added_by=added_by)
    db.add(book)
    db.flush()
    db.add(BookFile(book_id=book.id, file_path=f"/library/{book.id}/{title}.epub",
                    format="epub", file_size=1024))
    db.flush()
    return book


def _visible_ids(client: TestClient, token: str) -> set[int]:
    r = client.get("/api/books", params={"limit": 500},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"] if isinstance(body, dict) else body
    return {b["id"] for b in items}


def test_delete_user_with_uploads_and_personal_data(client, admin_user, db):
    admin, _ = admin_user
    gone = _make_user(db, "leaving")
    other = _make_user(db, "staying")
    other_token = create_access_token(subject=other.id)

    unfiled = _make_book(db, "Lanterns of the Quiet Reach", gone.id)
    private_lib = Library(name="Leaving's Shelf", is_public=False, owner_id=gone.id)
    db.add(private_lib)
    db.flush()
    in_private = _make_book(db, "The Saltglass Archive", gone.id)
    private_lib.books.append(in_private)
    public_lib = Library(name="Shared Stacks", is_public=True, owner_id=None)
    db.add(public_lib)
    db.flush()
    in_public = _make_book(db, "Ninefold Harbour", gone.id)
    public_lib.books.append(in_public)

    db.add(SavedFilter(name="My shelf", owner_id=gone.id, params="{}"))
    db.add(ApiToken(user_id=gone.id, name="script", prefix="abcd1234",
                    token_hash="0" * 64))
    db.add(UserBookStatus(user_id=gone.id, book_id=in_public.id, status="reading"))
    db.flush()

    before_other = _visible_ids(client, other_token)
    assert unfiled.id not in before_other
    assert in_private.id not in before_other
    assert in_public.id in before_other

    r = client.delete(f"/api/users/{gone.id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    assert db.get(User, gone.id) is None
    # Books survive and pass to the admin who deleted the user.
    for book in (unfiled, in_private, in_public):
        b = db.get(Book, book.id)
        assert b is not None
        assert b.added_by == admin.id
    # Personal data is gone.
    assert db.query(SavedFilter).filter(SavedFilter.owner_id == gone.id).count() == 0
    assert db.query(ApiToken).filter(ApiToken.user_id == gone.id).count() == 0
    assert db.query(UserBookStatus).filter(UserBookStatus.user_id == gone.id).count() == 0
    # The private library stays private and passes to the admin.
    lib = db.get(Library, private_lib.id)
    assert lib is not None and lib.owner_id == admin.id and lib.is_public is False

    # No formerly private book became visible to another member.
    after_other = _visible_ids(client, other_token)
    assert unfiled.id not in after_other
    assert in_private.id not in after_other
    assert in_public.id in after_other

    # Exactly one audit entry, written after the delete succeeded.
    entries = db.query(AuditLog).filter(AuditLog.action == "users.deleted").all()
    assert len(entries) == 1
    details = json.loads(entries[0].details)
    assert details["books_reassigned"] == 3
    assert details["libraries_reassigned"] == 1
    assert details["unfiled_books_filed_into_library_id"] is not None


def test_deleting_an_admin_keeps_their_unfiled_uploads_shared(client, admin_user, db):
    admin, _ = admin_user
    other_admin = _make_user(db, "formeradmin", role="admin")
    other_admin.is_admin = True
    member = _make_user(db, "reader")
    member_token = create_access_token(subject=member.id)
    shared = _make_book(db, "The Copper Meridian", other_admin.id)
    db.flush()
    assert shared.id in _visible_ids(client, member_token)

    r = client.delete(f"/api/users/{other_admin.id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    b = db.get(Book, shared.id)
    assert b.added_by == admin.id
    assert b.libraries == []
    assert shared.id in _visible_ids(client, member_token)


def test_delete_user_without_uploads_creates_no_library(client, admin_user, db):
    gone = _make_user(db, "quietleaver")
    count = db.query(Library).count()
    r = client.delete(f"/api/users/{gone.id}")
    assert r.status_code == 204, r.text
    assert db.query(Library).count() == count
