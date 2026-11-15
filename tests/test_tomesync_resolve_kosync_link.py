"""The plugin finds a book through the link a KOSync client already made.

A CrossInk reader sends progress for a sideloaded file; Tome keeps a book
record for it without a file. The same file on a TomeSync plugin device has
no content hash and no file path in Tome to match, so the lookup follows the
reader's document link: by the content id the plugin sends (the reader in
"binary" matching mode uses the same one) or by the MD5 of the file name
(the reader's default). A link that was only a guess is never followed.
"""
import hashlib

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password
from backend.models.book import BOOK_NO_FILE, Book
from backend.models.kosync import KOSyncDocumentMap
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User

CONTENT_ID = "cd" * 16
FILE_NAME = "Marrow Tide - Ines Calder.epub"
NAME_ID = hashlib.md5(FILE_NAME.encode()).hexdigest()


@pytest.fixture()
def plugin(db: Session):
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    user = User(username="twodevices", email="two@example.com",
                hashed_password=hash_password("pass"), is_active=True,
                role="member", must_change_password=False)
    db.add(user)
    db.flush()
    key = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(key), key_prefix=key[:11], label="t"))
    db.flush()
    with TestClient(app) as c:
        yield c, user, {"Authorization": f"Bearer {key}"}
    app.dependency_overrides.clear()


def _fileless(db: Session, user: User, title="A Book With No Words In Common") -> Book:
    book = Book(title=title, author="Ines Calder", status=BOOK_NO_FILE, added_by=user.id)
    db.add(book)
    db.flush()
    return book


def _link(db: Session, user: User, book: Book, document: str, guessed=None) -> None:
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=document, book_id=book.id, guessed=guessed))
    db.flush()


def _resolve(client, headers, filename=FILE_NAME, ko_md5=""):
    return client.get("/api/tome-sync/resolve", headers=headers,
                      params={"filename": filename, "ko_md5": ko_md5})


def test_same_file_name_finds_the_readers_book(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    _link(db, user, book, NAME_ID)
    r = _resolve(client, headers)
    assert r.status_code == 200
    assert r.json() == {"book_id": book.id, "method": "kosync_document"}


def test_a_path_in_front_of_the_name_does_not_matter(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    _link(db, user, book, NAME_ID)
    r = _resolve(client, headers, filename="/mnt/us/books/" + FILE_NAME)
    assert r.json()["book_id"] == book.id


def test_same_content_under_another_name_finds_the_readers_book(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    _link(db, user, book, CONTENT_ID)
    r = _resolve(client, headers, filename="renamed on the kindle.epub", ko_md5=CONTENT_ID)
    assert r.status_code == 200
    assert r.json()["book_id"] == book.id


def test_a_guessed_link_is_not_followed(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    _link(db, user, book, NAME_ID, guessed=True)
    assert _resolve(client, headers).status_code == 404


def test_another_users_link_is_not_followed(plugin, db):
    client, user, headers = plugin
    other = User(username="someoneelse", email="else@example.com",
                 hashed_password=hash_password("pass"), is_active=True,
                 role="member", must_change_password=False)
    db.add(other)
    db.flush()
    book = _fileless(db, other)
    _link(db, other, book, NAME_ID)
    assert _resolve(client, headers).status_code == 404


def test_a_book_the_user_can_no_longer_see_is_not_returned(plugin, db):
    client, user, headers = plugin
    admin = User(username="owner", email="owner@example.com",
                 hashed_password=hash_password("pass"), is_active=True,
                 is_admin=True, role="admin", must_change_password=False)
    db.add(admin)
    db.flush()
    private = Library(name="Private", is_public=False, owner_id=admin.id)
    db.add(private)
    book = Book(title="Hidden From The Reader", author="X", status="active", added_by=admin.id)
    book.libraries.append(private)
    db.add(book)
    db.flush()
    _link(db, user, book, NAME_ID)
    assert _resolve(client, headers).status_code == 404


def test_unknown_file_still_404s(plugin, db):
    client, _user, headers = plugin
    assert _resolve(client, headers, filename="nothing like it.epub", ko_md5="ef" * 16).status_code == 404


# ── a plugin device syncs reading for the user's own book without a file ─────

def test_position_round_trip_on_a_book_without_a_file(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    r = client.put(f"/api/tome-sync/position/{book.id}", headers=headers,
                   json={"progress": "/body/DocFragment[3]/body/p[2]", "percentage": 0.25, "device": "kindle"})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["percentage"] == 0.25


def test_session_is_accepted_for_a_book_without_a_file(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user)
    r = client.post("/api/tome-sync/session", headers=headers, json={
        "book_id": book.id, "started_at": "2026-11-15T10:00:00", "duration_seconds": 600,
    })
    assert r.status_code == 201, r.text


def test_another_users_book_without_a_file_stays_closed(plugin, db):
    client, user, headers = plugin
    other = User(username="readerowner", email="ro@example.com",
                 hashed_password=hash_password("pass"), is_active=True,
                 role="member", must_change_password=False)
    db.add(other)
    db.flush()
    book = _fileless(db, other)
    _link(db, user, book, NAME_ID)
    assert _resolve(client, headers).status_code == 404
    assert client.get(f"/api/tome-sync/position/{book.id}", headers=headers).status_code == 404
    r = client.put(f"/api/tome-sync/position/{book.id}", headers=headers,
                   json={"progress": "x", "percentage": 0.5, "device": "kindle"})
    assert r.status_code == 404


# ── another file name on the plugin device: the title in the name decides ───

def test_title_in_the_file_name_finds_the_book_without_a_file(plugin, db):
    client, user, headers = plugin
    book = _fileless(db, user, title="Marrow Tide")
    _link(db, user, book, NAME_ID)
    r = _resolve(client, headers, filename="Calder, Ines - Marrow Tide (retail).epub", ko_md5="12" * 16)
    assert r.status_code == 200
    assert r.json()["book_id"] == book.id


def test_title_match_never_reaches_another_users_book_without_a_file(plugin, db):
    client, user, headers = plugin
    other = User(username="thirdreader", email="third@example.com",
                 hashed_password=hash_password("pass"), is_active=True,
                 role="member", must_change_password=False)
    db.add(other)
    db.flush()
    _fileless(db, other, title="Marrow Tide")
    assert _resolve(client, headers, filename="Marrow Tide.epub").status_code == 404


# ── the reader's position reaches the plugin without any server setting ─────

def _reader_push(db, user, book, pct, locator="/body/DocFragment[4]/body/p[9]/text().0"):
    import time
    from backend.models.kosync import KOSyncProgress, KOSyncUser
    acc = KOSyncUser(username=f"ks-{user.username}", userkey="k" * 32, user_id=user.id)
    db.add(acc)
    db.flush()
    db.add(KOSyncProgress(user_id=acc.id, document=NAME_ID, progress=locator,
                          percentage=pct, device="CrossInk", timestamp=int(time.time())))
    db.flush()


def test_reader_position_is_served_for_a_book_without_a_file(plugin, db):
    from backend.core.config import settings
    assert settings.kosync_position_bridge is False
    client, user, headers = plugin
    book = _fileless(db, user)
    _link(db, user, book, NAME_ID)
    _reader_push(db, user, book, 0.43)
    r = client.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["percentage"] == 0.43
    assert r.json()["device"] == "CrossInk"


def test_library_books_still_need_the_setting(plugin, db, make_book):
    client, user, headers = plugin
    book = make_book(title="A Library Book")
    _link(db, user, book, NAME_ID)
    _reader_push(db, user, book, 0.43)
    assert client.get(f"/api/tome-sync/position/{book.id}", headers=headers).status_code == 404


# ── two devices on one book: the figures add up ──────────────────────────────

def test_reader_and_plugin_sittings_add_up_on_one_book(plugin, db):
    """A sitting on the plugin device joins the reader's on the same book:
    time and pages are summed, the pace is pages over all of that time."""
    import time
    from datetime import datetime, timedelta
    from backend.core.security import create_access_token
    from tests.test_kosync_book_pages import _delivered, _item, _put_books, _session
    from backend.models.kosync import KOSyncUser

    client, user, headers = plugin
    book = _fileless(db, user, title="Two Devices")
    doc = "0123456789abcdef0123456789abcdef"
    _link(db, user, book, doc)
    acc = KOSyncUser(username=user.username, userkey="k" * 32, user_id=user.id)
    db.add(acc)
    db.flush()
    now = int(time.time())
    _session(client, acc, seconds=3000, start=now - 3 * 86400, session_id="a" * 32)
    assert _delivered(_put_books(client, acc, _item(pages=60, seconds=3000, sessions=1)), book_count=1)
    _session(client, acc, seconds=3000, start=now - 2 * 86400, session_id="b" * 32)
    assert _delivered(_put_books(client, acc, _item(pages=120, seconds=6000, sessions=2)), book_count=1)

    r = client.post("/api/tome-sync/session", headers=headers, json={
        "book_id": book.id, "started_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
        "duration_seconds": 1800, "pages_turned": 30, "device": "Kindle",
        "progress_start": 0.4, "progress_end": 0.5})
    assert r.status_code == 201, r.text

    jwt = {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}
    own = client.get(f"/api/books/{book.id}/reading-stats", headers=jwt).json()["own"]
    assert own["total_seconds"] == 7800
    assert own["sessions"] == 3
    assert own["pages_turned"] == 150
    assert own["pace_pages_per_min"] == pytest.approx(150 / 130, abs=0.01)
