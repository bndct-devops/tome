"""Regression test for issue #88.

The plugin's series browser used a single, series-level `book_type` to file
every download in a batch. For a real series that's fine (all volumes share a
type), but the "__unserialized__" (No Series) bucket aggregates standalone books
of *mixed* types — so standalone RoyalRoad books were filed under whatever type
the alphabetically-first standalone happened to be (e.g. light_novel).

The fix makes `/tome-sync/series/{book_id}` emit a per-book `book_type` so the
plugin can file each download under its own type. The series-level `book_type`
stays for backwards compatibility with older plugin builds.
"""
from backend.models.library import BookType
from backend.models.tome_sync import ApiKey


def _api_key_for(db, user_id: int) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def _book_type(db, slug: str, label: str, sort_order: int) -> BookType:
    bt = BookType(slug=slug, label=label, sort_order=sort_order)
    db.add(bt)
    db.flush()
    return bt


def test_unserialized_bucket_returns_per_book_type(db, client, admin_user, make_book):
    """Mixed-type standalone books each carry their own book_type, not the
    bucket's first book's type."""
    user, _ = admin_user
    ln = _book_type(db, "light_novel", "Light Novels", 1)
    rr = _book_type(db, "royalroad", "RoyalRoad", 2)

    # Alphabetical order puts the light-novel first, which is exactly the book
    # whose type used to be stamped on the whole bucket.
    apex = make_book(title="Apex Predator", series=None)        # light_novel
    apex.book_type_id = ln.id
    royal = make_book(title="Zenith Climb", series=None)        # royalroad
    royal.book_type_id = rr.id
    db.flush()

    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    resp = client.get(f"/api/tome-sync/series/{royal.id}", headers=hdr)
    assert resp.status_code == 200
    data = resp.json()

    assert data["series_name"] == "__unserialized__"
    by_title = {b["title"]: b for b in data["books"]}
    # The bug: every book reported the bucket type. The fix: own types.
    assert by_title["Zenith Climb"]["book_type"] == "royalroad"
    assert by_title["Apex Predator"]["book_type"] == "light_novel"


def test_real_series_reports_per_book_type_and_legacy_top_level(db, client, admin_user, make_book):
    """A real series still exposes the legacy series-level `book_type` (for old
    plugins) and now also a matching per-book `book_type`."""
    user, _ = admin_user
    manga = _book_type(db, "manga", "Manga", 1)

    v1 = make_book(title="One Piece, Vol. 1", series="One Piece", series_index=1)
    v1.book_type_id = manga.id
    v2 = make_book(title="One Piece, Vol. 2", series="One Piece", series_index=2)
    v2.book_type_id = manga.id
    db.flush()

    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    resp = client.get(f"/api/tome-sync/series/{v1.id}", headers=hdr)
    assert resp.status_code == 200
    data = resp.json()

    assert data["series_name"] == "One Piece"
    assert data["book_type"] == "manga"  # legacy series-level field preserved
    assert all(b["book_type"] == "manga" for b in data["books"])


def test_untyped_standalone_reports_book(db, client, admin_user, make_book):
    """A standalone with no type reports "book", never null (issue #259: null
    crashed single-book downloads) and never the bucket's first book's type."""
    user, _ = admin_user
    ln = _book_type(db, "light_novel", "Light Novels", 1)
    first = make_book(title="Apex Predator", series=None)
    first.book_type_id = ln.id
    typeless = make_book(title="Orphan Title", series=None)
    db.flush()

    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    resp = client.get(f"/api/tome-sync/series/{typeless.id}", headers=hdr)
    assert resp.status_code == 200
    book = next(b for b in resp.json()["books"] if b["title"] == "Orphan Title")
    assert book["book_type"] == "book"


def test_untyped_volume_reports_its_series_type(db, client, admin_user, make_book):
    """An untyped volume of a typed series reports the series type, so a
    single-volume download files next to its siblings."""
    user, _ = admin_user
    manga = _book_type(db, "manga", "Manga", 1)
    v1 = make_book(title="Vol 1", series="Ink Tide", series_index=1)
    v1.book_type_id = manga.id
    make_book(title="Vol 2", series="Ink Tide", series_index=2)
    db.flush()

    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    resp = client.get(f"/api/tome-sync/series/{v1.id}", headers=hdr)
    assert resp.status_code == 200
    assert [b["book_type"] for b in resp.json()["books"]] == ["manga", "manga"]


def test_untyped_book_in_author_and_search_lists_reports_book(db, client, admin_user, make_book):
    """The mixed lists (author, search) have no series type to borrow."""
    user, _ = admin_user
    make_book(title="Orphan Title", author="Quill Marrow", series=None)
    db.flush()

    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    by_author = client.get("/api/tome-sync/author-books", params={"author": "Quill Marrow"}, headers=hdr)
    search = client.get("/api/tome-sync/search", params={"q": "Orphan"}, headers=hdr)
    for resp in (by_author, search):
        assert resp.status_code == 200, resp.text
        assert [b["book_type"] for b in resp.json()["books"]] == ["book"]
