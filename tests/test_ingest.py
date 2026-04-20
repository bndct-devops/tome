"""Tests for POST /api/books/check-hashes and POST /api/books/ingest.

Covers:
- check-hashes happy path
- check-hashes visibility: guest cannot learn about private books
- ingest happy path: file saved, book created, is_reviewed=True, library assignment
- ingest book_type_id auto-library assignment
- ingest dedupe: same file twice -> 409 with existing_id
- ingest permissions: guest gets 403
- ingest via API token auth (proves universal token-auth scope is wired correctly)
"""
import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.api_token import ApiToken
from backend.models.book import Book, BookFile
from backend.models.library import Library, BookType
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
        can_upload=True,
        can_download=True,
    )
    db.add(perms)
    db.flush()

    token = create_access_token(subject=user.id)
    return user, token


def _make_guest(db: Session, username: str = "guest1") -> tuple[User, str]:
    """Insert a guest-role user and return (user, jwt_token)."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("guestpass"),
        is_active=True,
        is_admin=False,
        role="guest",
        must_change_password=False,
    )
    db.add(user)
    db.flush()

    perms = UserPermission(user_id=user.id)
    db.add(perms)
    db.flush()

    token = create_access_token(subject=user.id)
    return user, token


def _make_api_token(db: Session, user: User) -> str:
    """Create a raw API token (plaintext) and persist its hash in DB."""
    plaintext = "tome_testtoken12345"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    db.add(ApiToken(
        user_id=user.id,
        name="Test Token",
        token_hash=token_hash,
        prefix="testtok",
    ))
    db.flush()
    return plaintext


def _fake_epub_bytes() -> bytes:
    """Minimal EPUB-like bytes (just needs to be non-empty for hash purposes)."""
    return b"PK\x03\x04fake epub content for testing"


def _upload_response(client: TestClient, filename: str = "Test Book.epub",
                     content: bytes | None = None,
                     meta: dict | None = None,
                     headers: dict | None = None):
    """Helper to call POST /api/books/ingest."""
    if content is None:
        content = _fake_epub_bytes()
    if meta is None:
        meta = {"title": "Test Book", "author": "Test Author"}
    return client.post(
        "/api/books/ingest",
        files={"file": (filename, io.BytesIO(content), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
        **({"headers": headers} if headers else {}),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def patched_client(db: Session, admin_user: tuple[User, str]):
    """TestClient with filesystem operations patched so no real files are written."""
    user, token = admin_user

    from backend.main import create_app
    from backend.core.database import get_db

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        library_dir = tmpdir_path / "library"
        incoming_dir = tmpdir_path / "incoming"
        library_dir.mkdir()
        incoming_dir.mkdir()

        with (
            patch("backend.api.books.settings") as mock_settings,
            patch("backend.services.organizer.sanitize_name", side_effect=lambda s: s or "Unknown"),
        ):
            mock_settings.library_dir = library_dir
            mock_settings.incoming_dir = incoming_dir
            mock_settings.covers_dir = tmpdir_path / "covers"
            mock_settings.covers_dir.mkdir()

            with TestClient(app, raise_server_exceptions=True) as c:
                c.headers["Authorization"] = f"Bearer {token}"
                yield c, db, user, token, library_dir

    app.dependency_overrides.clear()


# ── check-hashes tests ────────────────────────────────────────────────────────


def test_check_hashes_empty_list(client: TestClient):
    """Empty hash list returns empty existing map."""
    resp = client.post("/api/books/check-hashes", json={"hashes": []})
    assert resp.status_code == 200
    assert resp.json() == {"existing": {}}


def test_check_hashes_happy_path(client: TestClient, db: Session, make_book):
    """Hashes matching existing books are returned in the existing map."""
    book = make_book(title="Hash Book", content_hash="abc123def456")
    db.flush()

    resp = client.post(
        "/api/books/check-hashes",
        json={"hashes": ["abc123def456", "nonexistent000"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "abc123def456" in body["existing"]
    assert body["existing"]["abc123def456"] == book.id
    assert "nonexistent000" not in body["existing"]


def test_check_hashes_via_book_file_hash(client: TestClient, db: Session, make_book):
    """Hashes matching BookFile.content_hash (not Book.content_hash) are also found."""
    book = make_book(title="File Hash Book", content_hash=None)
    # Set the BookFile's content_hash directly
    bf = db.query(BookFile).filter(BookFile.book_id == book.id).first()
    bf.content_hash = "filehash999"
    db.flush()

    resp = client.post(
        "/api/books/check-hashes",
        json={"hashes": ["filehash999"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "filehash999" in body["existing"]
    assert body["existing"]["filehash999"] == book.id


def test_check_hashes_guest_cannot_see_private_book(db: Session, client: TestClient, make_book, admin_user):
    """A guest user should not learn about books that are not publicly visible."""
    # Create a book uploaded by admin but NOT in any public library
    admin, _admin_token = admin_user
    # By default make_book creates a book with admin as owner;
    # guest CAN see admin-uploaded books per visibility rules.
    # To test the restriction, we need a member-uploaded book not in a public library.
    member, member_token = _make_member(db, username="privmember")

    private_book = Book(
        title="Private Book",
        author="Private Author",
        content_hash="privatehash001",
        status="active",
        added_by=member.id,
    )
    db.add(private_book)
    db.flush()
    db.add(BookFile(
        book_id=private_book.id,
        file_path=f"/library/{private_book.id}/private.epub",
        format="epub",
        file_size=512,
        content_hash="privatehash001",
    ))
    db.flush()

    guest, guest_token = _make_guest(db, username="vis_guest")

    resp = client.post(
        "/api/books/check-hashes",
        json={"hashes": ["privatehash001"]},
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Guest should NOT see the member-uploaded private book
    assert "privatehash001" not in body["existing"]


def test_check_hashes_admin_sees_all(db: Session, client: TestClient, make_book, admin_user):
    """Admin can find any hash regardless of ownership."""
    member, _mt = _make_member(db, username="member_for_admin_check")
    private_book = Book(
        title="Member Private",
        content_hash="member_private_hash",
        status="active",
        added_by=member.id,
    )
    db.add(private_book)
    db.flush()

    resp = client.post(
        "/api/books/check-hashes",
        json={"hashes": ["member_private_hash"]},
        # client fixture already uses admin credentials
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "member_private_hash" in body["existing"]


# ── ingest tests ──────────────────────────────────────────────────────────────


def test_ingest_happy_path(patched_client):
    """Ingest creates a book with is_reviewed=True and correct metadata."""
    c, db, _user, _token, library_dir = patched_client

    content = _fake_epub_bytes()
    meta = {
        "title": "Ingest Book",
        "author": "Ingest Author",
        "year": 2023,
        "tags": ["fiction", "adventure"],
    }
    resp = c.post(
        "/api/books/ingest",
        files={"file": ("ingest.epub", io.BytesIO(content), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Ingest Book"
    assert body["author"] == "Ingest Author"
    assert body["year"] == 2023
    assert {t["tag"] for t in body["tags"]} == {"fiction", "adventure"}

    # Verify is_reviewed=True in DB
    book = db.query(Book).filter(Book.id == body["id"]).first()
    assert book is not None
    assert book.is_reviewed is True


def test_ingest_file_written_to_library(patched_client):
    """The uploaded file is moved into the library directory."""
    c, db, _user, _token, library_dir = patched_client

    content = b"epub content for write test"
    meta = {"title": "Written Book", "author": "Write Author"}
    resp = c.post(
        "/api/books/ingest",
        files={"file": ("writebook.epub", io.BytesIO(content), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp.status_code == 201

    book_id = resp.json()["id"]
    book = db.query(Book).filter(Book.id == book_id).first()
    bf = db.query(BookFile).filter(BookFile.book_id == book_id).first()
    assert bf is not None
    dest = Path(bf.file_path)
    assert dest.exists()
    assert dest.read_bytes() == content


def test_ingest_library_ids_assignment(patched_client, db: Session):
    """Books are attached to specified library_ids after ingest."""
    c, db, user, _token, library_dir = patched_client

    lib = Library(name="My Library", owner_id=user.id)
    db.add(lib)
    db.flush()

    meta = {"title": "Lib Book", "library_ids": [lib.id]}
    resp = c.post(
        "/api/books/ingest",
        files={"file": ("libbook.epub", io.BytesIO(b"epub lib"), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert lib.id in body["library_ids"]


def test_ingest_book_type_auto_library(patched_client, db: Session):
    """When book_type_id is provided and no library_ids, the book is placed in
    the BookType's auto-created library."""
    c, db, user, _token, library_dir = patched_client

    # Create a BookType with an auto-library
    auto_lib = Library(name="Novels Library", owner_id=None, is_public=True)
    db.add(auto_lib)
    db.flush()

    bt = BookType(slug="novels-test", label="Novels Test", library_id=auto_lib.id)
    db.add(bt)
    db.flush()

    meta = {"title": "Novel Book", "book_type_id": bt.id}
    resp = c.post(
        "/api/books/ingest",
        files={"file": ("novel.epub", io.BytesIO(b"epub novel"), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert auto_lib.id in body["library_ids"]


def test_ingest_dedupe_returns_409(patched_client):
    """Uploading the same file content twice returns 409 with existing_id."""
    c, _db, _user, _token, _library_dir = patched_client

    content = b"duplicate epub content unique bytes 98765"
    meta = {"title": "Dupe Book"}

    # First upload
    resp1 = c.post(
        "/api/books/ingest",
        files={"file": ("dupe.epub", io.BytesIO(content), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp1.status_code == 201
    existing_id = resp1.json()["id"]

    # Second upload with same content
    resp2 = c.post(
        "/api/books/ingest",
        files={"file": ("dupe.epub", io.BytesIO(content), "application/epub+zip")},
        data={"metadata": json.dumps(meta)},
    )
    assert resp2.status_code == 409
    body = resp2.json()
    detail = body["detail"]
    assert detail["detail"] == "duplicate"
    assert detail["existing_id"] == existing_id


def test_ingest_guest_gets_403(patched_client, db: Session):
    """Guest role cannot call /ingest — gets 403."""
    c, db, _user, _token, _library_dir = patched_client

    guest, guest_token = _make_guest(db, username="ingest_guest")

    resp = c.post(
        "/api/books/ingest",
        files={"file": ("guest.epub", io.BytesIO(b"epub guest"), "application/epub+zip")},
        data={"metadata": json.dumps({"title": "Guest Book"})},
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert resp.status_code == 403


def test_ingest_via_api_token(patched_client, db: Session):
    """Ingest works when authenticated with an API token (not a JWT)."""
    c, db, user, _token, _library_dir = patched_client

    api_token_plaintext = _make_api_token(db, user)

    resp = c.post(
        "/api/books/ingest",
        files={"file": ("token_auth.epub", io.BytesIO(b"epub via token auth"), "application/epub+zip")},
        data={"metadata": json.dumps({"title": "Token Auth Book"})},
        headers={"Authorization": f"Bearer {api_token_plaintext}"},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Token Auth Book"


def test_ingest_invalid_metadata_json_returns_422(patched_client):
    """Malformed metadata JSON returns 422."""
    c, _db, _user, _token, _library_dir = patched_client

    resp = c.post(
        "/api/books/ingest",
        files={"file": ("bad.epub", io.BytesIO(b"epub bad"), "application/epub+zip")},
        data={"metadata": "not json at all"},
    )
    assert resp.status_code == 422


def test_ingest_missing_title_returns_422(patched_client):
    """Metadata without required 'title' field returns 422."""
    c, _db, _user, _token, _library_dir = patched_client

    resp = c.post(
        "/api/books/ingest",
        files={"file": ("notitle.epub", io.BytesIO(b"epub no title"), "application/epub+zip")},
        data={"metadata": json.dumps({"author": "Someone"})},
    )
    assert resp.status_code == 422


def test_ingest_unsupported_format_returns_400(patched_client):
    """Uploading a .txt file returns 400."""
    c, _db, _user, _token, _library_dir = patched_client

    resp = c.post(
        "/api/books/ingest",
        files={"file": ("book.txt", io.BytesIO(b"plain text"), "text/plain")},
        data={"metadata": json.dumps({"title": "Text Book"})},
    )
    assert resp.status_code == 400
