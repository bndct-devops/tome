"""Visibility regression tests for the OPDS feed (issue #53).

OPDS previously used its own visibility copy (an inner join on libraries) and
had no coverage. It now routes through the shared
``backend.core.permissions.book_visibility_filter`` like every other surface,
so private-library books are hidden, unfiled admin books stay visible, and a
member's own unfiled upload stays private.
"""
from sqlalchemy.orm import Session

from backend.core.security import hash_password, create_access_token, get_current_user_basic
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.user import User


def _make_user(db: Session, username: str, role: str, is_admin: bool = False) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=is_admin,
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def _make_book(db: Session, title: str, added_by: int | None,
               series: str | None = None) -> Book:
    book = Book(title=title, author="Auth", series=series, status="active", added_by=added_by)
    db.add(book)
    db.flush()
    db.add(BookFile(book_id=book.id, file_path=f"/library/{book.id}/{title}.epub",
                    format="epub", file_size=1024))
    db.flush()
    return book


def _make_lib(db: Session, name: str, is_public: bool, owner_id: int | None = None) -> Library:
    lib = Library(name=name, is_public=is_public, owner_id=owner_id)
    db.add(lib)
    db.flush()
    return lib


def _as(client, user: User):
    client.app.dependency_overrides[get_current_user_basic] = lambda: user


def _clear(client):
    client.app.dependency_overrides.pop(get_current_user_basic, None)


def _all_books_text(client, user: User) -> str:
    _as(client, user)
    try:
        r = client.get("/opds/all")
        assert r.status_code == 200, r.text
        return r.text
    finally:
        _clear(client)


def test_opds_hides_private_admin_book_from_member(client, db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "opds_mem", "member")
    priv = _make_lib(db, "OPDS Priv", is_public=False, owner_id=admin.id)
    pub = _make_lib(db, "OPDS Pub", is_public=True, owner_id=admin.id)
    private_book = _make_book(db, "ZZ Private Title", added_by=admin.id)
    public_book = _make_book(db, "ZZ Public Title", added_by=admin.id)
    private_book.libraries.append(priv)
    public_book.libraries.append(pub)
    db.flush()

    body = _all_books_text(client, member)
    assert "ZZ Public Title" in body
    assert "ZZ Private Title" not in body


def test_opds_hides_private_admin_book_from_guest(client, db, admin_user):
    admin, _ = admin_user
    guest = _make_user(db, "opds_gst", "guest")
    priv = _make_lib(db, "OPDS Priv G", is_public=False, owner_id=admin.id)
    private_book = _make_book(db, "ZZ Guest Hidden", added_by=admin.id)
    private_book.libraries.append(priv)
    db.flush()
    body = _all_books_text(client, guest)
    assert "ZZ Guest Hidden" not in body


def test_opds_shows_unfiled_admin_book(client, db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "opds_mem2", "member")
    book = _make_book(db, "ZZ Unfiled Admin", added_by=admin.id)  # no library
    db.flush()
    assert "ZZ Unfiled Admin" in _all_books_text(client, member)


def test_opds_hides_member_unfiled_upload_from_others(client, db, admin_user):
    member_a = _make_user(db, "opds_a", "member")
    member_b = _make_user(db, "opds_b", "member")
    book = _make_book(db, "ZZ A Draft", added_by=member_a.id)  # no library
    db.flush()
    assert "ZZ A Draft" in _all_books_text(client, member_a)       # owner sees own
    assert "ZZ A Draft" not in _all_books_text(client, member_b)   # other member does not


def test_opds_member_sees_owned_private_library(client, db, admin_user):
    member = _make_user(db, "opds_owner", "member")
    priv = _make_lib(db, "Owned Priv", is_public=False, owner_id=member.id)
    book = _make_book(db, "ZZ In Owned Lib", added_by=member.id)
    book.libraries.append(priv)
    db.flush()
    assert "ZZ In Owned Lib" in _all_books_text(client, member)


def test_opds_series_feed_excludes_private(client, db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "opds_series", "member")
    priv = _make_lib(db, "OPDS Priv S", is_public=False, owner_id=admin.id)
    book = _make_book(db, "ZZ Secret Vol", added_by=admin.id, series="ZZ Secret OPDS Series")
    book.libraries.append(priv)
    db.flush()
    _as(client, member)
    try:
        r = client.get("/opds/series")
        assert r.status_code == 200, r.text
        assert "ZZ Secret OPDS Series" not in r.text
    finally:
        _clear(client)


def test_opds_libraries_nav_hides_others_private(client, db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "opds_libnav", "member")
    other = _make_user(db, "opds_other", "member")
    others_priv = _make_lib(db, "Someone Elses Private", is_public=False, owner_id=other.id)
    owned = _make_lib(db, "My Own Private", is_public=False, owner_id=member.id)
    db.flush()
    _as(client, member)
    try:
        r = client.get("/opds/libraries")
        assert r.status_code == 200, r.text
        assert "My Own Private" in r.text
        assert "Someone Elses Private" not in r.text
    finally:
        _clear(client)
