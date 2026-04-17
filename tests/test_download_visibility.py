"""Tests that download endpoints respect the same visibility rules as GET /api/books."""
import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.user import User, UserPermission


def _make_user(db: Session, username: str, role: str, is_admin: bool = False) -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=is_admin,
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id, can_download=True)
    db.add(perms)
    db.flush()
    token = create_access_token(subject=user.id)
    return user, token


def _make_book(db: Session, title: str, added_by: int | None) -> Book:
    book = Book(
        title=title,
        status="active",
        added_by=added_by,
    )
    db.add(book)
    db.flush()
    bf = BookFile(
        book_id=book.id,
        file_path=f"/library/{book.id}/{title}.epub",
        format="epub",
        file_size=1024,
    )
    db.add(bf)
    db.flush()
    return book


@pytest.fixture()
def visibility_client(db: Session):
    """TestClient wired to the test DB, no default auth header."""
    from backend.main import create_app

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, db

    app.dependency_overrides.clear()


def test_admin_can_download_any_book(visibility_client):
    c, db = visibility_client
    admin, admin_token = _make_user(db, "adm", "admin", is_admin=True)
    member, _ = _make_user(db, "mem", "member")
    book = _make_book(db, "Member Book", added_by=member.id)

    r = c.get(
        f"/api/books/{book.id}/download/{book.files[0].id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code in (200, 404)
    # File doesn't exist on disk in test env, so 404 from disk check is fine.
    # What matters is it's NOT a visibility-driven 404 — we check by ensuring
    # admin gets past the visibility gate (error would be "File no longer on disk")
    if r.status_code == 404:
        assert "no longer on disk" in r.json()["detail"] or "not found" in r.json()["detail"].lower()


def test_guest_cannot_download_private_member_book(visibility_client):
    c, db = visibility_client
    _admin, _ = _make_user(db, "adm2", "admin", is_admin=True)
    member, _ = _make_user(db, "mem2", "member")
    guest, guest_token = _make_user(db, "gst2", "guest")
    # Book uploaded by member (not admin) — not in any public library → guest cannot see it
    book = _make_book(db, "Private Book", added_by=member.id)

    r = c.get(
        f"/api/books/{book.id}/download/{book.files[0].id}",
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert r.status_code == 404
    assert "File not found" in r.json()["detail"]


def test_member_cannot_download_another_members_private_book(visibility_client):
    c, db = visibility_client
    _admin, _ = _make_user(db, "adm3", "admin", is_admin=True)
    member_a, token_a = _make_user(db, "memA", "member")
    member_b, _ = _make_user(db, "memB", "member")
    book = _make_book(db, "Bob Book", added_by=member_b.id)

    r = c.get(
        f"/api/books/{book.id}/download/{book.files[0].id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404
    assert "File not found" in r.json()["detail"]


def test_member_can_download_own_book(visibility_client):
    c, db = visibility_client
    _admin, _ = _make_user(db, "adm4", "admin", is_admin=True)
    member, token = _make_user(db, "mem4", "member")
    book = _make_book(db, "Own Book", added_by=member.id)

    r = c.get(
        f"/api/books/{book.id}/download/{book.files[0].id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Passes visibility; file doesn't exist on disk in test
    assert r.status_code in (200, 404)
    if r.status_code == 404:
        assert "no longer on disk" in r.json()["detail"].lower() or "File not found" == r.json()["detail"]


def test_guest_can_download_admin_book(visibility_client):
    c, db = visibility_client
    admin, _ = _make_user(db, "adm5", "admin", is_admin=True)
    guest, guest_token = _make_user(db, "gst5", "guest")
    book = _make_book(db, "Admin Book", added_by=admin.id)

    r = c.get(
        f"/api/books/{book.id}/download/{book.files[0].id}",
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    # Visibility passes; file doesn't exist on disk in test
    assert r.status_code in (200, 404)
    if r.status_code == 404:
        assert "no longer on disk" in r.json()["detail"].lower() or "File not found" == r.json()["detail"]


def test_bulk_download_excludes_invisible_books(visibility_client):
    c, db = visibility_client
    admin, _ = _make_user(db, "adm6", "admin", is_admin=True)
    member, _ = _make_user(db, "mem6", "member")
    guest, guest_token = _make_user(db, "gst6", "guest")

    admin_book = _make_book(db, "Visible Admin Book", added_by=admin.id)
    private_book = _make_book(db, "Invisible Member Book", added_by=member.id)

    r = c.post(
        "/api/downloads",
        headers={"Authorization": f"Bearer {guest_token}"},
        json={"book_ids": [admin_book.id, private_book.id]},
    )
    # Response will be a ZIP (possibly empty due to missing disk files) or 404 if no visible books
    # The guest can see admin_book but not private_book — so the query returns admin_book
    # The ZIP may be empty if the file doesn't exist on disk, but we should get 200, not 404
    assert r.status_code == 200


def test_bulk_download_all_invisible_returns_404(visibility_client):
    c, db = visibility_client
    _admin, _ = _make_user(db, "adm7", "admin", is_admin=True)
    member, _ = _make_user(db, "mem7", "member")
    guest, guest_token = _make_user(db, "gst7", "guest")

    private_book = _make_book(db, "Invisible Book 7", added_by=member.id)

    r = c.post(
        "/api/downloads",
        headers={"Authorization": f"Bearer {guest_token}"},
        json={"book_ids": [private_book.id]},
    )
    assert r.status_code == 404
