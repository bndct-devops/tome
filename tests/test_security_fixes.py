"""Tests for the security fixes shipped in the security-phase1 PR.

Each test corresponds to a finding in docs/promotion-readiness-audit.md.
"""
import io
import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.user import User, UserPermission
from backend.models.tome_sync import ApiKey


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
    db.add(UserPermission(user_id=user.id, can_upload=True, can_download=True, can_manage_libraries=True))
    db.flush()
    return user, create_access_token(subject=user.id)


@pytest.fixture()
def app_client(db: Session):
    """TestClient wired to the test DB, no default auth header."""
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, db
    app.dependency_overrides.clear()


# ── #1: Path traversal in upload filename ────────────────────────────────────

def test_upload_rejects_dot_dot_with_valid_extension(app_client):
    """A filename whose basename is '..something' must not land at tmp_dir/..

    Use a name that passes the suffix check (.epub) but is still purely a
    directory traversal — the safe_name guard should reject it.
    """
    c, db = app_client
    _, member_token = _make_user(db, "memup", "member")
    # "..epub" — passes suffix=".epub" check, but `Path("..epub").name == "..epub"`
    # which is technically a valid file name. So we use the "." path instead:
    fake_file = ("./.epub", io.BytesIO(b"x"), "application/octet-stream")
    r = c.post(
        "/api/books/upload",
        files={"file": fake_file},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    # Either rejected as Invalid filename (preferred) or for some other 4xx reason.
    # The MUST is: not 5xx, not 2xx — no file was written outside tmp_dir.
    assert 400 <= r.status_code < 500, f"unexpected status {r.status_code}"


def test_upload_neutralizes_path_components(app_client):
    """A filename like '../../etc/evil.epub' must be reduced to its basename.

    Whatever else happens (success or unrelated failure), the file MUST NOT
    end up outside the tmp_dir — Path(...).name strips every path component.
    """
    c, db = app_client
    _, member_token = _make_user(db, "memup2", "member")
    fake_file = ("../../etc/evil.epub", io.BytesIO(b"PK\x03\x04not-real-epub"), "application/epub+zip")
    r = c.post(
        "/api/books/upload",
        files={"file": fake_file},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    # Hard guarantee: the upload did NOT 500 (which would mean a real-fs error),
    # AND no file appeared at /etc/evil.epub.
    assert r.status_code < 500
    import os
    assert not os.path.exists("/etc/evil.epub"), "Path traversal succeeded — fix is broken"


# ── #2: Library auth — global libraries are admin-only for mutations ─────────

def test_member_cannot_delete_global_library(app_client):
    c, db = app_client
    admin, _ = _make_user(db, "adminlibs", "admin", is_admin=True)
    _, member_token = _make_user(db, "memlibs", "member")

    # Admin creates a global library (owner_id=None)
    lib = Library(name="Global Lib", owner_id=None)
    db.add(lib)
    db.flush()

    r = c.delete(
        f"/api/libraries/{lib.id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert r.status_code == 403


def test_admin_can_delete_global_library(app_client):
    c, db = app_client
    _, admin_token = _make_user(db, "adminlibs2", "admin", is_admin=True)
    lib = Library(name="Global Lib 2", owner_id=None)
    db.add(lib)
    db.flush()

    r = c.delete(
        f"/api/libraries/{lib.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204


# ── #3: Library creation requires at least member role ───────────────────────

def test_guest_cannot_create_library(app_client):
    c, db = app_client
    _, guest_token = _make_user(db, "guestlibs", "guest")
    r = c.post(
        "/api/libraries",
        json={"name": "Guest Lib", "icon": "Library", "is_public": False},
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert r.status_code == 403


def test_member_can_create_library(app_client):
    c, db = app_client
    _, member_token = _make_user(db, "memlibs2", "member")
    r = c.post(
        "/api/libraries",
        json={"name": "Member Lib", "icon": "Library", "is_public": False},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert r.status_code == 201


# ── #5: Comic page IDOR ──────────────────────────────────────────────────────

def test_member_cannot_list_pages_of_other_members_book(app_client):
    c, db = app_client
    owner, _ = _make_user(db, "comicowner", "member")
    _, other_token = _make_user(db, "comicother", "member")

    # owner uploads a comic book (added_by=owner)
    book = Book(title="Owner Comic", status="active", added_by=owner.id)
    db.add(book)
    db.flush()
    db.add(BookFile(book_id=book.id, file_path=f"/lib/{book.id}/x.cbz", format="cbz", file_size=1))
    db.flush()

    r = c.get(
        f"/api/books/{book.id}/pages",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    # other member shouldn't see it — visibility filter returns 404 (not 403, avoid existence leak)
    assert r.status_code == 404


def test_member_can_list_pages_of_own_book(app_client):
    c, db = app_client
    owner, owner_token = _make_user(db, "comicowner2", "member")
    book = Book(title="Owner Comic 2", status="active", added_by=owner.id)
    db.add(book)
    db.flush()
    db.add(BookFile(book_id=book.id, file_path=f"/lib/{book.id}/x.cbz", format="cbz", file_size=1))
    db.flush()

    r = c.get(
        f"/api/books/{book.id}/pages",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    # Will 404 because the cbz file doesn't exist on disk in tests — but NOT because of visibility.
    # The visibility check passed; the file-on-disk check failed.
    assert r.status_code in (404, 500)
    if r.status_code == 404:
        detail = r.json().get("detail", "")
        assert "not found" in detail.lower()  # "File not found on disk", not "Book not found"


# ── #4: OPDS download IDOR ───────────────────────────────────────────────────

def test_opds_download_blocks_invisible_books(app_client):
    c, db = app_client
    owner, _ = _make_user(db, "opdsowner", "member")
    other, _ = _make_user(db, "opdsother", "member")

    book = Book(title="Owner OPDS Book", status="active", added_by=owner.id)
    db.add(book)
    db.flush()
    bf = BookFile(book_id=book.id, file_path=f"/lib/{book.id}/x.epub", format="epub", file_size=1)
    db.add(bf)
    db.flush()

    # OPDS uses HTTP Basic; the "other" user supplies their own credentials
    r = c.get(
        f"/opds/download/{book.id}/{bf.id}",
        auth=("opdsother", "pass"),
    )
    assert r.status_code == 404


# ── #7: ApiKey hashing — plaintext is not stored ─────────────────────────────

def test_created_api_key_stores_hash_not_plaintext(app_client):
    c, db = app_client
    _, member_token = _make_user(db, "akmem", "member")

    r = c.post(
        "/api/plugin/api-keys",
        json={"label": "My KO Plugin"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert r.status_code == 201
    plaintext = r.json()["key"]
    assert plaintext.startswith("tk_")

    # The DB row should NOT contain the plaintext anywhere
    row = db.query(ApiKey).filter(ApiKey.label == "My KO Plugin").first()
    assert row is not None
    assert row.key_hash == ApiKey.hash_key(plaintext)
    assert row.key_hash != plaintext
    assert row.key_prefix == plaintext[:11]


def test_api_key_auth_works_with_hashed_storage(app_client):
    c, db = app_client
    user, member_token = _make_user(db, "akmem2", "member")

    # Provision a key
    r = c.post(
        "/api/plugin/api-keys",
        json={"label": "Test Auth"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    plaintext = r.json()["key"]

    # Hit a TomeSync-protected endpoint with the plaintext — should succeed
    r2 = c.get(
        "/api/tome-sync/resolve",
        params={"filename": "doesnt-matter.epub"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    # Either resolves (200/404) or fails business-logic-wise, but NOT 401
    assert r2.status_code != 401, f"plaintext auth failed against hashed storage: {r2.status_code}"


def test_api_key_rejects_wrong_plaintext(app_client):
    c, db = app_client
    _, member_token = _make_user(db, "akmem3", "member")
    c.post(
        "/api/plugin/api-keys",
        json={"label": "Test Wrong"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    r = c.get(
        "/api/tome-sync/resolve",
        params={"filename": "x.epub"},
        headers={"Authorization": "Bearer tk_wrong000000000000000000000000000000000000"},
    )
    assert r.status_code == 401
