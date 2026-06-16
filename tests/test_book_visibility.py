"""Regression tests for book visibility (issue #53).

Before the fix, every admin-uploaded book was visible to all members/guests
regardless of library membership (`Book.added_by.in_(admin_ids)`), so placing
an admin's book in a *private* library did not hide it. TomeSync's series
browser applied no visibility filter at all, leaking the whole catalogue.

The rule now: library membership is the gate. A book in a private library is
visible only to the owner, its assigned users, and admins. A book in *no*
library falls back to the legacy shared-collection rule — admin/legacy unfiled
books stay public; a member's own unfiled upload stays private to them.
"""
from sqlalchemy.orm import Session

from backend.core.security import hash_password, create_access_token
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User


def _make_user(db: Session, username: str, role: str, is_admin: bool = False) -> tuple[User, str]:
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
    return user, create_access_token(subject=user.id)


def _make_book(db: Session, title: str, added_by: int | None,
               series: str | None = None, series_index: float | None = None) -> Book:
    book = Book(
        title=title,
        author="Some Author",
        series=series,
        series_index=series_index,
        status="active",
        added_by=added_by,
    )
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


def _file_in(db: Session, book: Book, lib: Library) -> None:
    book.libraries.append(lib)
    db.flush()


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _book_ids(client, token: str) -> set[int]:
    r = client.get("/api/books", headers=_hdr(token))
    assert r.status_code == 200, r.text
    return {b["id"] for b in r.json()}


def _series_names(client, token: str) -> set[str]:
    r = client.get("/api/books/series", headers=_hdr(token))
    assert r.status_code == 200, r.text
    return {s["name"] for s in r.json()}


def _api_key_hdr(db: Session, user_id: int) -> dict:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext), label="test"))
    db.flush()
    return {"Authorization": f"Bearer {plaintext}"}


# ── The core bug: admin book in a private library ───────────────────────────────

def test_admin_book_in_private_library_hidden_from_member(client, db, admin_user):
    """Issue #53: an admin-uploaded book in a private-only library must NOT be
    visible to a member who isn't assigned to that library."""
    admin, admin_token = admin_user
    _member, m_token = _make_user(db, "mem53", "member")

    priv = _make_lib(db, "Admin Private", is_public=False, owner_id=admin.id)
    book = _make_book(db, "Hidden Admin Book", added_by=admin.id)
    _file_in(db, book, priv)

    assert book.id not in _book_ids(client, m_token)
    assert client.get(f"/api/books/{book.id}", headers=_hdr(m_token)).status_code == 404
    # Admin still sees it
    assert book.id in _book_ids(client, admin_token)


def test_admin_book_in_private_library_hidden_from_guest(client, db, admin_user):
    admin, _ = admin_user
    _guest, g_token = _make_user(db, "gst53", "guest")
    priv = _make_lib(db, "Admin Private G", is_public=False, owner_id=admin.id)
    book = _make_book(db, "Hidden From Guest", added_by=admin.id)
    _file_in(db, book, priv)
    assert book.id not in _book_ids(client, g_token)
    assert client.get(f"/api/books/{book.id}", headers=_hdr(g_token)).status_code == 404


def test_public_library_book_visible_to_everyone(client, db, admin_user):
    admin, _ = admin_user
    _member, m_token = _make_user(db, "mem_pub", "member")
    _guest, g_token = _make_user(db, "gst_pub", "guest")
    pub = _make_lib(db, "Public", is_public=True, owner_id=admin.id)
    book = _make_book(db, "Public Book", added_by=admin.id)
    _file_in(db, book, pub)
    assert book.id in _book_ids(client, m_token)
    assert book.id in _book_ids(client, g_token)


def test_book_in_both_public_and_private_is_visible(client, db, admin_user):
    """Public membership wins even when the book is also in a private library."""
    admin, _ = admin_user
    _member, m_token = _make_user(db, "mem_both", "member")
    pub = _make_lib(db, "Public Both", is_public=True, owner_id=admin.id)
    priv = _make_lib(db, "Private Both", is_public=False, owner_id=admin.id)
    book = _make_book(db, "Dual Book", added_by=admin.id)
    _file_in(db, book, pub)
    _file_in(db, book, priv)
    assert book.id in _book_ids(client, m_token)


# ── No-library fallback (scoped option A) ───────────────────────────────────────

def test_admin_unfiled_book_visible_to_all(client, db, admin_user):
    admin, _ = admin_user
    _member, m_token = _make_user(db, "mem_unf", "member")
    _guest, g_token = _make_user(db, "gst_unf", "guest")
    book = _make_book(db, "Admin Unfiled", added_by=admin.id)  # no library
    assert book.id in _book_ids(client, m_token)
    assert book.id in _book_ids(client, g_token)


def test_legacy_unfiled_book_visible_to_all(client, db, admin_user):
    _member, m_token = _make_user(db, "mem_leg", "member")
    book = _make_book(db, "Legacy Book", added_by=None)  # no uploader, no library
    assert book.id in _book_ids(client, m_token)


def test_member_unfiled_upload_private_to_uploader(client, db, admin_user):
    """A member's own unfiled upload stays visible to them but not other members."""
    member_a, a_token = _make_user(db, "memA53", "member")
    _member_b, b_token = _make_user(db, "memB53", "member")
    book = _make_book(db, "A's Draft", added_by=member_a.id)  # no library
    assert book.id in _book_ids(client, a_token)        # uploader sees own
    assert book.id not in _book_ids(client, b_token)    # other member does not


# ── Owner / assigned access to private libraries ────────────────────────────────

def test_member_sees_books_in_library_they_own(client, db, admin_user):
    member, m_token = _make_user(db, "owner53", "member")
    priv = _make_lib(db, "My Private", is_public=False, owner_id=member.id)
    # Book uploaded by admin but filed into the member's own private library
    admin, _ = admin_user
    book = _make_book(db, "In My Library", added_by=admin.id)
    _file_in(db, book, priv)
    assert book.id in _book_ids(client, m_token)


def test_member_sees_books_in_assigned_library(client, db, admin_user):
    admin, _ = admin_user
    member, m_token = _make_user(db, "assigned53", "member")
    priv = _make_lib(db, "Shared Private", is_public=False, owner_id=admin.id)
    priv.assigned_users.append(member)
    db.flush()
    book = _make_book(db, "Assigned Book", added_by=admin.id)
    _file_in(db, book, priv)
    assert book.id in _book_ids(client, m_token)


# ── Aggregation surfaces (series / facets) ──────────────────────────────────────

def test_series_listing_excludes_private(client, db, admin_user):
    admin, _ = admin_user
    _member, m_token = _make_user(db, "mem_series", "member")
    pub = _make_lib(db, "Pub S", is_public=True, owner_id=admin.id)
    priv = _make_lib(db, "Priv S", is_public=False, owner_id=admin.id)
    pub_book = _make_book(db, "Pub V1", added_by=admin.id, series="Visible Series", series_index=1)
    priv_book = _make_book(db, "Priv V1", added_by=admin.id, series="Secret Series", series_index=1)
    _file_in(db, pub_book, pub)
    _file_in(db, priv_book, priv)

    names = _series_names(client, m_token)
    assert "Visible Series" in names
    assert "Secret Series" not in names


def test_facets_exclude_private_series_and_authors(client, db, admin_user):
    admin, _ = admin_user
    _member, m_token = _make_user(db, "mem_facet", "member")
    priv = _make_lib(db, "Priv F", is_public=False, owner_id=admin.id)
    book = _make_book(db, "Secret Facet", added_by=admin.id, series="Secret Facet Series")
    _file_in(db, book, priv)

    r = client.get("/api/books/facets", headers=_hdr(m_token))
    assert r.status_code == 200, r.text
    assert "Secret Facet Series" not in r.json()["series"]


# ── TomeSync series browser (was completely unfiltered) ─────────────────────────

def test_tomesync_series_browser_excludes_private(client, db, admin_user):
    admin, _ = admin_user
    member, _ = _make_user(db, "mem_ts", "member")
    pub = _make_lib(db, "Pub TS", is_public=True, owner_id=admin.id)
    priv = _make_lib(db, "Priv TS", is_public=False, owner_id=admin.id)
    pub_book = _make_book(db, "TS Pub", added_by=admin.id, series="TS Visible", series_index=1)
    priv_book = _make_book(db, "TS Priv", added_by=admin.id, series="TS Secret", series_index=1)
    _file_in(db, pub_book, pub)
    _file_in(db, priv_book, priv)

    hdr = _api_key_hdr(db, member.id)
    r = client.get("/api/tome-sync/series", headers=hdr)
    assert r.status_code == 200, r.text
    names = {s["name"] for s in r.json()}
    assert "TS Visible" in names
    assert "TS Secret" not in names

    # Direct series fetch on the private book's id must 404
    r2 = client.get(f"/api/tome-sync/series/{priv_book.id}", headers=hdr)
    assert r2.status_code == 404


def test_tomesync_series_books_filtered(client, db, admin_user):
    """Within a visible series, volumes that live only in a private library are
    excluded from the per-series book list."""
    admin, _ = admin_user
    member, _ = _make_user(db, "mem_ts2", "member")
    pub = _make_lib(db, "Pub TS2", is_public=True, owner_id=admin.id)
    priv = _make_lib(db, "Priv TS2", is_public=False, owner_id=admin.id)
    v1 = _make_book(db, "Mixed V1", added_by=admin.id, series="Mixed Series", series_index=1)
    v2 = _make_book(db, "Mixed V2", added_by=admin.id, series="Mixed Series", series_index=2)
    _file_in(db, v1, pub)
    _file_in(db, v2, priv)

    hdr = _api_key_hdr(db, member.id)
    r = client.get(f"/api/tome-sync/series/{v1.id}", headers=hdr)
    assert r.status_code == 200, r.text
    ids = {b["id"] for b in r.json()["books"]}
    assert v1.id in ids
    assert v2.id not in ids
