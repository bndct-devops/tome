"""The web Library shows the user's own books without a file.

A book without a file (``BOOK_NO_FILE``) is made when a reader reports reading
a file Tome does not have. Its owner should find it in their Library grid and
search, so ``GET /api/books`` (and ``/api/books/ids``, which powers select-all)
take an opt-in ``include_no_file``. What these tests hold on to:

- without the opt-in nothing changes (other clients: iOS, Scribe, the classic
  UI never send it);
- with it, only the caller's own file-less books are added, for admins too;
- every filter and sort still works with them in the set, without dropping or
  repeating rows;
- bulk actions on a selection holding one do not break, and it stays private.
"""
import io
import zipfile

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.book import BOOK_NO_FILE, Book, BookFile
from backend.models.library import Library
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.fts import index_book


# ── helpers ───────────────────────────────────────────────────────────────────

def _member(db, name):
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
                is_active=True, role="member", must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _no_file(db, owner, title, author="Ilse Varga", series=None, series_index=None):
    book = Book(title=title, author=author, series=series, series_index=series_index,
                status=BOOK_NO_FILE, is_reviewed=True, added_by=owner.id)
    db.add(book)
    db.flush()
    return book


def _active(make_book, db, title, **kw):
    book = make_book(title=title, **kw)
    index_book(db, book)  # make_book does not index; the list search needs it
    db.flush()
    return book


def _list(client, headers=None, **params):
    r = client.get("/api/books", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = [b["id"] for b in rows]
    assert len(ids) == len(set(ids)), f"duplicate rows: {ids}"
    return rows, ids, int(r.headers["x-total-count"])


def _ids(client, headers=None, **params):
    r = client.get("/api/books/ids", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    return set(r.json()["ids"])


@pytest.fixture()
def world(db, admin_user, make_book):
    """Admin with two active books and a file-less one; two members with a
    file-less book each (the same title for both, to prove nothing leaks)."""
    admin, _ = admin_user
    alice, alice_h = _member(db, "alice")
    bob, bob_h = _member(db, "bob")
    a1 = _active(make_book, db, "The Copper Lantern", author="Mara Quill", year=2001,
                 series="Lantern Cycle", series_index=1)
    a2 = _active(make_book, db, "Salt and Ember", author="Teodor Brann", year=2015, tags=["fantasy"])
    nf_admin = _no_file(db, admin, "Admins Night Reading", author="Oren Lisk")
    nf_alice = _no_file(db, alice, "Glass Orchard", author="Ilse Varga",
                        series="Lantern Cycle", series_index=2)
    nf_bob = _no_file(db, bob, "Glass Orchard", author="Ilse Varga")
    db.commit()
    return {
        "admin": admin, "alice": alice, "alice_h": alice_h, "bob": bob, "bob_h": bob_h,
        "a1": a1, "a2": a2, "nf_admin": nf_admin, "nf_alice": nf_alice, "nf_bob": nf_bob,
    }


# ── default unchanged ─────────────────────────────────────────────────────────

def test_default_list_has_no_file_less_books(client, world):
    w = world
    for headers in (None, w["alice_h"]):
        rows, ids, total = _list(client, headers)
        assert set(ids) == {w["a1"].id, w["a2"].id}
        assert total == 2
        assert all(b["status"] == "active" for b in rows)
        assert _ids(client, headers) == {w["a1"].id, w["a2"].id}
    # Searching the exact title of a file-less book finds nothing without the opt-in.
    _, ids, total = _list(client, w["alice_h"], q="Glass Orchard")
    assert ids == [] and total == 0


def test_include_no_file_false_is_the_default(client, world):
    _, ids, _ = _list(client, world["alice_h"], include_no_file="false")
    assert world["nf_alice"].id not in ids


# ── opt-in: own books only ────────────────────────────────────────────────────

def test_member_sees_own_file_less_book_only(client, world):
    w = world
    rows, ids, total = _list(client, w["alice_h"], include_no_file="true")
    assert set(ids) == {w["a1"].id, w["a2"].id, w["nf_alice"].id}
    assert total == 3
    mine = next(b for b in rows if b["id"] == w["nf_alice"].id)
    assert mine["status"] == "no_file"
    assert mine["files"] == []
    assert _ids(client, w["alice_h"], include_no_file="true") == set(ids)

    _, bob_ids, _ = _list(client, w["bob_h"], include_no_file="true")
    assert set(bob_ids) == {w["a1"].id, w["a2"].id, w["nf_bob"].id}


def test_admin_sees_own_file_less_book_not_members(client, world):
    w = world
    _, ids, total = _list(client, include_no_file="true")
    assert set(ids) == {w["a1"].id, w["a2"].id, w["nf_admin"].id}
    assert total == 3
    assert _ids(client, include_no_file="true") == set(ids)
    # Not even by uploader filter or search.
    _, ids, _ = _list(client, include_no_file="true", added_by=w["alice"].id)
    assert w["nf_alice"].id not in ids
    _, ids, _ = _list(client, include_no_file="true", q="Glass")
    assert ids == []


def test_tag_restriction_still_hides_own_file_less_book(client, db, world):
    from backend.models.book import BookTag
    w = world
    db.add(BookTag(book_id=w["nf_alice"].id, tag="horror", source="user"))
    db.commit()
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true")
    assert w["nf_alice"].id in ids
    w["alice"].excluded_tags = '["Horror"]'
    db.commit()
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true")
    assert w["nf_alice"].id not in ids


# ── search ────────────────────────────────────────────────────────────────────

def test_search_finds_own_file_less_book(client, world):
    w = world
    h = w["alice_h"]
    for q in ("Glass", "orchard", "glass varga", "Varga", "Lantern Cycle"):
        _, ids, _ = _list(client, h, include_no_file="true", q=q)
        assert w["nf_alice"].id in ids, q
        assert w["nf_bob"].id not in ids, q
    # Every term must match (AND), like the FTS search.
    _, ids, _ = _list(client, h, include_no_file="true", q="Glass Brann")
    assert w["nf_alice"].id not in ids
    # Active books still come from the FTS index alongside.
    _, ids, _ = _list(client, h, include_no_file="true", q="Lantern")
    assert set(ids) == {w["a1"].id, w["nf_alice"].id}
    # LIKE wildcards in the query are literal, not match-everything.
    _, ids, _ = _list(client, h, include_no_file="true", q="%")
    assert w["nf_alice"].id not in ids
    _, ids, _ = _list(client, h, include_no_file="true", q="Nothing Like This")
    assert ids == []


@pytest.fixture()
def accented(db, admin_user, make_book, world):
    """An active book and Alice's file-less book with the same accented
    title words, author and series."""
    active = _active(make_book, db, "Ölmühle am Fluss", author="Émile Dorán", series="Città Vecchia")
    nf = _no_file(db, world["alice"], "Ölmühle im Tal", author="Émile Dorán", series="Città Vecchia")
    db.commit()
    return active, nf


@pytest.mark.parametrize("q", [
    "ölmühle", "Ölmühle", "olmuhle", "OLMUHLE", "mühle", "muhle", "émile", "emile", "EMILE",
    "doran", "Dorán", "citta", "Città", "vecchia", "olmuhle emile", "Öl", "öl", "ol", "ci",
    "olmuhle brann", "zzz",
])
def test_accented_search_matches_file_less_like_active(client, accented, world, q):
    active, nf = accented
    _, ids, _ = _list(client, world["alice_h"], include_no_file="true", q=q)
    assert (active.id in ids) == (nf.id in ids), (q, ids)


def test_accented_search_finds_both(client, accented, world):
    active, nf = accented
    for q in ("ölmühle", "olmuhle", "OLMUHLE", "émile", "emile", "doran", "citta vecchia"):
        _, ids, _ = _list(client, world["alice_h"], include_no_file="true", q=q)
        assert {active.id, nf.id} <= set(ids), q
    _, ids, _ = _list(client, world["alice_h"], include_no_file="true", q="olmuhle brann")
    assert active.id not in ids and nf.id not in ids
    # Bob never gets Alice's record, accents or not.
    _, ids, _ = _list(client, world["bob_h"], include_no_file="true", q="olmuhle")
    assert ids == [active.id]


# ── sorts ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sort", ["title", "author", "year", "added_at", "rating", "status_updated"])
@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("grouped", [False, True])
def test_every_sort_keeps_the_file_less_book_once(client, world, sort, order, grouped):
    w = world
    params = {"include_no_file": "true", "sort": sort, "order": order}
    if grouped:
        params["group_by_series"] = "true"
    rows, ids, total = _list(client, w["alice_h"], **params)
    assert ids.count(w["nf_alice"].id) == 1
    assert total == len(ids)
    if not grouped:
        assert set(ids) == {w["a1"].id, w["a2"].id, w["nf_alice"].id}


def test_sort_by_title_places_it_by_its_series(client, world):
    w = world
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true", sort="title", order="asc")
    # "Lantern Cycle" #1 then #2 (the file-less volume), then "Salt and Ember".
    assert ids == [w["a1"].id, w["nf_alice"].id, w["a2"].id]


def test_status_updated_sort_with_reading_filter(client, db, world):
    w = world
    db.add(UserBookStatus(user_id=w["alice"].id, book_id=w["nf_alice"].id, status="reading", progress_pct=0.4))
    db.add(UserBookStatus(user_id=w["alice"].id, book_id=w["a2"].id, status="reading", progress_pct=0.1))
    db.commit()
    for order in ("asc", "desc"):
        _, ids, _ = _list(client, w["alice_h"], include_no_file="true", reading_status="reading",
                          sort="status_updated", order=order)
        assert set(ids) == {w["nf_alice"].id, w["a2"].id}


# ── filters ───────────────────────────────────────────────────────────────────

def test_filters_with_file_less_books(client, db, world):
    w = world
    h = w["alice_h"]
    nf = w["nf_alice"].id

    def ids_for(**p):
        _, ids, total = _list(client, h, include_no_file="true", **p)
        assert total == len(ids)
        assert _ids(client, h, include_no_file="true", **p) == set(ids), p
        return set(ids)

    # Things it has: author, series, language-less, volume content type.
    assert ids_for(author="Ilse Varga") == {nf}
    assert ids_for(series="Lantern Cycle") == {w["a1"].id, nf}
    assert nf not in ids_for(no_series="true")
    assert nf in ids_for(content_type="volume")
    assert nf in ids_for(ownership="mine")
    # Things it lacks: a file format, tags, a library, a language.
    assert ids_for(format="epub") == {w["a1"].id, w["a2"].id}
    assert ids_for(tag="fantasy") == {w["a2"].id}
    assert ids_for(language="en") == {w["a1"].id, w["a2"].id}
    # Missing fields: it has no cover.
    assert nf in ids_for(missing="cover")
    assert nf in ids_for(missing="any")
    assert nf not in ids_for(missing="author")
    # "Missing: File" = the user's books without a file, nothing else.
    assert ids_for(missing="file") == {nf}
    _, ids, _ = _list(client, h, missing="file")
    assert ids == []
    # Reading status.
    assert nf in ids_for(reading_status="unread")
    db.add(UserBookStatus(user_id=w["alice"].id, book_id=nf, status="read", rating=4))
    db.commit()
    assert ids_for(reading_status="read") == {nf}
    assert nf not in ids_for(reading_status="unread")
    assert ids_for(min_rating=4) == {nf}
    # Device filters do not crash; it is on no recorded device.
    assert nf in ids_for(not_on_device="Kobo")
    assert ids_for(on_device="Kobo") == set()


def test_ownership_shared_leaves_out_an_admins_own_record(client, world):
    w = world
    _, ids, _ = _list(client, include_no_file="true", ownership="shared")
    assert set(ids) == {w["a1"].id, w["a2"].id}


def test_library_filter_and_grouping(client, db, world):
    w = world
    lib = Library(name="Shelf of Lamps", is_public=True, owner_id=None)
    lib.books.append(w["a1"])
    db.add(lib)
    db.commit()
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true", library_id=lib.id)
    assert ids == [w["a1"].id]

    # Grouped: the file-less volume of "Lantern Cycle" stands alone and does
    # not inflate the series stack (the series page does not list it).
    rows, ids, _ = _list(client, w["alice_h"], include_no_file="true", group_by_series="true")
    by_id = {b["id"]: b for b in rows}
    assert set(ids) == {w["a1"].id, w["a2"].id, w["nf_alice"].id}
    assert by_id[w["a1"].id]["series_count"] == 1
    assert by_id[w["nf_alice"].id]["series_count"] == 1
    assert by_id[w["nf_alice"].id]["stack_cover_ids"] == []


# ── bulk actions ──────────────────────────────────────────────────────────────

def test_bulk_download_of_only_file_less_books_is_refused(client, world):
    w = world
    r = client.post("/api/downloads", headers=w["alice_h"], json={"book_ids": [w["nf_alice"].id]})
    assert r.status_code == 400
    assert "file" in r.json()["detail"].lower()


def test_bulk_download_skips_file_less_books(client, db, world, tmp_path):
    w = world
    real = tmp_path / "copper.epub"
    real.write_bytes(b"not a real epub")
    bf = db.query(BookFile).filter(BookFile.book_id == w["a1"].id).one()
    bf.file_path = str(real)
    db.commit()
    r = client.post("/api/downloads", json={"book_ids": [w["a1"].id, w["nf_admin"].id]})
    assert r.status_code == 200, r.text
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 1 and names[0].endswith("copper.epub")


def test_bulk_metadata_on_a_file_less_book(client, db, world):
    w = world
    r = client.put("/api/books/bulk-metadata", headers=w["alice_h"],
                   json={"book_ids": [w["nf_alice"].id, w["a2"].id], "tags_add": ["to-buy"]})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    db.expire_all()
    book = db.get(Book, w["nf_alice"].id)
    assert book.status == BOOK_NO_FILE
    assert [t.tag for t in book.tags] == ["to-buy"]
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true", tag="to-buy")
    assert set(ids) == {w["nf_alice"].id, w["a2"].id}


def test_add_to_library_skips_file_less_books(client, db, world):
    w = world
    lib = Library(name="Open Stacks", is_public=True, owner_id=None)
    db.add(lib)
    db.commit()
    r = client.post(f"/api/libraries/{lib.id}/books", json={"book_ids": [w["nf_admin"].id, w["a2"].id]})
    assert r.status_code == 204, r.text
    db.expire_all()
    assert [b.id for b in db.get(Library, lib.id).books] == [w["a2"].id]
    assert db.get(Book, w["nf_admin"].id).libraries == []
    # Other users' counts hold only books they can open.
    listed = client.get("/api/libraries", headers=w["alice_h"]).json()
    assert next(x for x in listed if x["id"] == lib.id)["book_count"] == 1
    # A selection of only file-less books adds nothing and does not fail.
    r = client.post(f"/api/libraries/{lib.id}/books", json={"book_ids": [w["nf_admin"].id]})
    assert r.status_code == 204
    db.expire_all()
    assert len(db.get(Library, lib.id).books) == 1


def test_member_add_to_own_library_skips_file_less_books(client, db, world):
    w = world
    lib = Library(name="Alices Shelf", is_public=False, owner_id=w["alice"].id)
    db.add(lib)
    db.commit()
    r = client.post(f"/api/libraries/{lib.id}/books", headers=w["alice_h"],
                    json={"book_ids": [w["nf_alice"].id, w["a1"].id]})
    assert r.status_code == 204, r.text
    db.expire_all()
    assert [b.id for b in db.get(Library, lib.id).books] == [w["a1"].id]


def test_book_type_does_not_put_a_file_less_book_in_its_library(client, db, world):
    from backend.models.library import BookType
    w = world
    bt = BookType(slug="novels-nf", label="Novels")
    db.add(bt)
    db.commit()
    r = client.put("/api/books/bulk-metadata", json={"book_ids": [w["nf_admin"].id, w["a2"].id], "book_type_id": bt.id})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/books/{w['nf_admin'].id}", json={"book_type_id": bt.id})
    assert r.status_code == 200, r.text
    db.expire_all()
    nf = db.get(Book, w["nf_admin"].id)
    assert nf.book_type_id == bt.id  # the type is metadata and still set
    assert nf.libraries == []
    assert [lib.id for lib in db.get(Book, w["a2"].id).libraries] == [db.get(BookType, bt.id).library_id]


def test_file_less_book_put_in_a_library_directly_stays_private(client, db, world):
    w = world
    lib = Library(name="Open Stacks", is_public=True, owner_id=None)
    lib.books.append(w["nf_admin"])
    lib.books.append(w["a2"])
    db.add(lib)
    db.commit()
    # The owner finds it under the library.
    _, ids, _ = _list(client, include_no_file="true", library_id=lib.id)
    assert set(ids) == {w["nf_admin"].id, w["a2"].id}
    # Nobody else does, anywhere.
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true", library_id=lib.id)
    assert ids == [w["a2"].id]
    _, ids, _ = _list(client, w["alice_h"], include_no_file="true")
    assert w["nf_admin"].id not in ids
    assert client.get(f"/api/books/{w['nf_admin'].id}", headers=w["alice_h"]).status_code in (403, 404)


def test_bulk_delete_with_a_file_less_book(client, db, world):
    w = world
    r = client.post("/api/books/bulk-delete", headers=w["alice_h"],
                    json={"book_ids": [w["nf_alice"].id, w["nf_bob"].id]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == [w["nf_alice"].id]
    assert [e["book_id"] for e in body["errors"]] == [w["nf_bob"].id]
    db.expire_all()
    assert db.get(Book, w["nf_alice"].id) is None
    assert db.get(Book, w["nf_bob"].id) is not None
