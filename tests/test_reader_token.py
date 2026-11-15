"""Book-scoped reader tokens: the web reader's <img> and file URLs carry a
short-lived token that opens one book's reader files and nothing else, so the
login token stays out of URLs. The old style (login token in ?token=) keeps
working for native clients."""
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.security import _signing_key, create_access_token, hash_password
from backend.models.user import User, UserPermission

ADMIN = {"username": "testadmin", "password": "adminpass123"}


@pytest.fixture()
def reader_books(tmp_path: Path, make_book):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("001.jpg", b"\xff\xd8\xff\xd9")
    cbz2 = tmp_path / "other.cbz"
    with zipfile.ZipFile(cbz2, "w") as zf:
        zf.writestr("001.jpg", b"\xff\xd8\xff\xd9")
    return {
        "pdf": make_book(title="Lantern Ledger", file_path=str(pdf), file_format="pdf"),
        "epub": make_book(title="Quiet Harbour", file_path=str(epub), file_format="epub"),
        "cbz": make_book(title="Paper Moths", file_path=str(cbz), file_format="cbz"),
        "other": make_book(title="Glass Orchard", file_path=str(cbz2), file_format="cbz"),
    }


def _urls(book_id: int) -> list[str]:
    return [
        f"/api/books/{book_id}/pages/0",
        f"/api/books/{book_id}/read.cbz",
        f"/api/books/{book_id}/read.pdf",
        f"/api/books/{book_id}/read.epub",
    ]


def _file_urls(books) -> list[tuple[int, str]]:
    return [
        (books["cbz"].id, f"/api/books/{books['cbz'].id}/pages/0"),
        (books["cbz"].id, f"/api/books/{books['cbz'].id}/read.cbz"),
        (books["pdf"].id, f"/api/books/{books['pdf'].id}/read.pdf"),
        (books["epub"].id, f"/api/books/{books['epub'].id}/read.epub"),
    ]


def _mint(client: TestClient, book_id: int, headers: dict | None = None) -> str:
    r = client.post(f"/api/books/{book_id}/reader-token", headers=headers or {})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] == 12 * 3600
    return body["token"]


def test_reader_token_opens_its_books_reader_files(client: TestClient, reader_books):
    del client.headers["Authorization"]
    login = client.post("/api/auth/login", json=ADMIN).json()["access_token"]
    for book_id, url in _file_urls(reader_books):
        token = _mint(client, book_id, {"Authorization": f"Bearer {login}"})
        r = client.get(url, params={"token": token})
        assert r.status_code == 200, (url, r.status_code, r.text[:200])


def test_reader_token_is_refused_for_another_book(client: TestClient, reader_books):
    token = _mint(client, reader_books["cbz"].id)
    del client.headers["Authorization"]
    for url in _urls(reader_books["other"].id):
        assert client.get(url, params={"token": token}).status_code == 401, url


def test_reader_token_does_not_authenticate_api_routes(client: TestClient, reader_books):
    token = _mint(client, reader_books["cbz"].id)
    del client.headers["Authorization"]
    for path in ("/api/auth/me", f"/api/books/{reader_books['cbz'].id}", "/api/books"):
        r = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, (path, r.status_code)


def test_reader_token_expires(client: TestClient, reader_books, admin_user):
    user, _ = admin_user
    book_id = reader_books["cbz"].id
    expired = jwt.encode(
        {"sub": str(user.id), "typ": "reader", "book": book_id,
         "exp": datetime.now(timezone.utc) - timedelta(seconds=5)},
        _signing_key(), algorithm=settings.jwt_algorithm,
    )
    del client.headers["Authorization"]
    r = client.get(f"/api/books/{book_id}/pages/0", params={"token": expired})
    assert r.status_code == 401


def test_reader_token_from_a_login_dies_with_that_login(client: TestClient, reader_books):
    del client.headers["Authorization"]
    login = client.post("/api/auth/login", json=ADMIN).json()["access_token"]
    auth = {"Authorization": f"Bearer {login}"}
    book_id = reader_books["cbz"].id
    token = _mint(client, book_id, auth)
    assert client.get(f"/api/books/{book_id}/pages/0", params={"token": token}).status_code == 200
    assert client.post("/api/auth/logout", headers=auth).status_code == 204
    assert client.get(f"/api/books/{book_id}/pages/0", params={"token": token}).status_code == 401


def test_reader_token_needs_a_visible_book(client: TestClient, db: Session, reader_books):
    from backend.models.library import Library
    lib = Library(name="Private shelf", is_public=False, owner_id=None)
    db.add(lib)
    db.flush()
    reader_books["other"].libraries.append(lib)
    member = User(username="plainmember", email="pm@example.com",
                  hashed_password=hash_password("memberpass"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id, can_download=True))
    db.flush()
    token = create_access_token(subject=member.id)
    r = client.post(f"/api/books/{reader_books['other'].id}/reader-token",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_login_token_in_query_still_works(client: TestClient, reader_books):
    del client.headers["Authorization"]
    login = client.post("/api/auth/login", json=ADMIN).json()["access_token"]
    for _book_id, url in _file_urls(reader_books):
        assert client.get(url, params={"token": login}).status_code == 200, url


def test_authorization_header_works_without_query_token(client: TestClient, reader_books):
    # The client fixture sends an admin login token as a header.
    for _book_id, url in _file_urls(reader_books):
        assert client.get(url).status_code == 200, url


def test_no_token_at_all_is_refused(client: TestClient, reader_books):
    del client.headers["Authorization"]
    for _book_id, url in _file_urls(reader_books):
        assert client.get(url).status_code == 401, url
