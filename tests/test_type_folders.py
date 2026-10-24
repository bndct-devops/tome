"""Books of a type stored under a folder of their own (#270).

Covers:
- organizer: expected path with and without a type folder
- book-types API: folder validation, update leaves folder alone when omitted
- import: the type is decided before the move, so a cbz lands in the comic
  folder and an epub in the root layout; the import dialog's default type wins
- bindery accept and upload place by the chosen / detected type
- existing books are never moved: setting a folder makes them misplaced,
  Library Health lists them, reorganize dry run lists the move, the real run
  performs it and updates the stored path
- scan hint: a file under a top-level folder matching a type's folder or
  label gets that type, unless the cbz/manga rule already decided
- two types sharing one folder
"""
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.book import Book, BookFile
from backend.models.library import BookType
from backend.services.organizer import get_library_path
from backend.services.scanner import import_incoming, scan_library


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch):
    lib = tmp_path / "library"
    inc = tmp_path / "bindery"
    covers = tmp_path / "covers"
    for d in (lib, inc, covers):
        d.mkdir()
    monkeypatch.setattr("backend.core.config.settings.library_dir", lib)
    monkeypatch.setattr("backend.core.config.settings.incoming_dir", inc)
    return {"lib": lib, "inc": inc, "covers": covers}


@pytest.fixture()
def types(db: Session) -> dict[str, BookType]:
    out = {}
    for i, (slug, label) in enumerate(
        [("book", "Books"), ("manga", "Manga"), ("comic", "Comics"), ("light_novel", "Light Novels")]
    ):
        bt = BookType(slug=slug, label=label, sort_order=i)
        db.add(bt)
        out[slug] = bt
    db.flush()
    return out


def _fake_meta(monkeypatch, target: str, by_name: dict[str, dict]):
    """Replace extract_metadata in ``target`` with a lookup by file name."""
    def fake(path, *a, **kw):
        return dict(by_name.get(Path(path).name, {"title": Path(path).stem}))
    monkeypatch.setattr(target, fake)


# ── Organizer ────────────────────────────────────────────────────────────────

class TestOrganizer:
    META = {"title": "Akira", "series": "Akira", "series_index": 1.0}

    def test_no_folder_keeps_layout(self):
        assert get_library_path(self.META, "a.cbz") == Path("Akira/Akira v01.cbz")
        assert get_library_path(self.META, "a.cbz", "") == Path("Akira/Akira v01.cbz")
        assert get_library_path(self.META, "a.cbz", None) == Path("Akira/Akira v01.cbz")

    def test_folder_prefixes_every_layout(self):
        assert get_library_path(self.META, "a.cbz", "Manga") == Path("Manga/Akira/Akira v01.cbz")
        assert get_library_path({"title": "Dune", "author": "Frank Herbert", "year": 1965},
                                "d.epub", "Books") == Path("Books/Frank Herbert/Dune (1965).epub")
        assert get_library_path({"title": "Nameless"}, "n.pdf", "Comics") == Path("Comics/Unknown/Nameless.pdf")

    def test_folder_is_sanitized(self):
        # Validated on save; a bad stored value still cannot escape the library.
        rel = get_library_path(self.META, "a.cbz", "../x")
        assert ".." not in rel.parts
        assert len(rel.parts) == 3


# ── API validation ───────────────────────────────────────────────────────────

class TestBookTypeFolderApi:
    @pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "..", ".", ".hidden", "x" * 65])
    def test_rejects_bad_folder(self, client: TestClient, bad: str):
        r = client.post("/api/book-types", json={"label": "Weird", "folder": bad})
        assert r.status_code == 400, r.text

    @pytest.mark.parametrize("good,stored", [("", ""), ("Manga", "Manga"), ("  Comics  ", "Comics"),
                                             ("Light Novels", "Light Novels")])
    def test_accepts_folder(self, client: TestClient, good: str, stored: str):
        r = client.post("/api/book-types", json={"label": f"T {stored}", "folder": good})
        assert r.status_code == 201, r.text
        assert r.json()["folder"] == stored

    def test_default_is_empty_and_listed(self, client: TestClient):
        r = client.post("/api/book-types", json={"label": "Plain"})
        assert r.json()["folder"] == ""
        listed = client.get("/api/book-types").json()
        assert all("folder" in t for t in listed)

    def test_update_sets_clears_and_keeps(self, client: TestClient):
        bt_id = client.post("/api/book-types", json={"label": "Manga"}).json()["id"]
        r = client.put(f"/api/book-types/{bt_id}", json={"label": "Manga", "folder": "Manga"})
        assert r.status_code == 200 and r.json()["folder"] == "Manga"
        # A client that does not know the field must not wipe it.
        r = client.put(f"/api/book-types/{bt_id}", json={"label": "Manga!"})
        assert r.json()["folder"] == "Manga"
        r = client.put(f"/api/book-types/{bt_id}", json={"label": "Manga", "folder": "a/b"})
        assert r.status_code == 400
        r = client.put(f"/api/book-types/{bt_id}", json={"label": "Manga", "folder": ""})
        assert r.json()["folder"] == ""

    def test_seed_defaults_have_no_folder(self, db: Session):
        from backend.services.book_types import seed_book_types
        if db.query(BookType).count() == 0:
            seed_book_types(db)
        assert {bt.folder for bt in db.query(BookType).all()} == {""}


# ── Import places by type, before the move ───────────────────────────────────

class TestImportPlacement:
    def test_cbz_into_comic_folder_epub_into_root(self, db, dirs, types, monkeypatch):
        types["comic"].folder = "Comics"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {
            "Saga 01.cbz": {"title": "Saga", "series": "Saga", "series_index": 1.0},
            "Dune.epub": {"title": "Dune", "author": "Frank Herbert"},
        })
        (dirs["inc"] / "Saga 01.cbz").write_bytes(b"cbz-bytes")
        (dirs["inc"] / "Dune.epub").write_bytes(b"epub-bytes")

        res = import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db)
        assert res.added == 2, res.error_details

        assert (dirs["lib"] / "Comics/Saga/Saga v01.cbz").is_file()
        assert (dirs["lib"] / "Frank Herbert/Dune.epub").is_file()
        saga = db.query(Book).filter(Book.title == "Saga").one()
        assert saga.book_type_id == types["comic"].id
        assert saga.files[0].file_path == str((dirs["lib"] / "Comics/Saga/Saga v01.cbz").resolve())
        dune = db.query(Book).filter(Book.title == "Dune").one()
        assert dune.book_type_id is None

    def test_epub_into_book_folder_via_default_type(self, db, dirs, types, monkeypatch):
        types["book"].folder = "Books"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata",
                   {"Dune.epub": {"title": "Dune", "author": "Frank Herbert"}})
        (dirs["inc"] / "Dune.epub").write_bytes(b"epub-bytes")

        res = import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db,
                              default_type_id=types["book"].id)
        assert res.added == 1, res.error_details
        assert (dirs["lib"] / "Books/Frank Herbert/Dune.epub").is_file()
        assert db.query(Book).one().book_type_id == types["book"].id

    def test_import_endpoint_passes_default_type(self, client, db, dirs, types, monkeypatch):
        types["manga"].folder = "Manga"
        db.commit()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata",
                   {"Frieren 01.epub": {"title": "Frieren", "series": "Frieren", "series_index": 1.0}})
        (dirs["inc"] / "Frieren 01.epub").write_bytes(b"epub-bytes")
        r = client.post("/api/books/import", json={"default_type_id": types["manga"].id})
        assert r.status_code == 200, r.text
        assert (dirs["lib"] / "Manga/Frieren/Frieren v01.epub").is_file()

    def test_manga_flag_goes_to_manga_folder(self, db, dirs, types, monkeypatch):
        types["manga"].folder = "Manga"
        types["comic"].folder = "Comics"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {
            "Akira 01.cbz": {"title": "Akira", "series": "Akira", "series_index": 1.0, "_is_manga": True},
        })
        (dirs["inc"] / "Akira 01.cbz").write_bytes(b"cbz-bytes")
        import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db)
        assert (dirs["lib"] / "Manga/Akira/Akira v01.cbz").is_file()

    def test_two_types_share_a_folder(self, db, dirs, types, monkeypatch):
        types["manga"].folder = "Graphic"
        types["comic"].folder = "Graphic"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {
            "Akira 01.cbz": {"title": "Akira", "series": "Akira", "series_index": 1.0, "_is_manga": True},
            "Saga 01.cbz": {"title": "Saga", "series": "Saga", "series_index": 1.0},
        })
        (dirs["inc"] / "Akira 01.cbz").write_bytes(b"cbz-1")
        (dirs["inc"] / "Saga 01.cbz").write_bytes(b"cbz-2")
        import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db)
        assert (dirs["lib"] / "Graphic/Akira/Akira v01.cbz").is_file()
        assert (dirs["lib"] / "Graphic/Saga/Saga v01.cbz").is_file()
        by_title = {b.title: b.book_type_id for b in db.query(Book).all()}
        assert by_title == {"Akira": types["manga"].id, "Saga": types["comic"].id}


class TestBinderyAndUpload:
    def test_bindery_accept_uses_item_type_folder(self, client, db, dirs, types, monkeypatch):
        types["light_novel"].folder = "Light Novels"
        db.commit()
        monkeypatch.setattr("backend.api.bindery.extract_metadata", lambda *a, **kw: {"title": "Spice"})
        (dirs["inc"] / "Spice 01.epub").write_bytes(b"epub-bytes")
        r = client.post("/api/bindery/accept", json={"files": [{
            "path": "Spice 01.epub", "title": "Spice and Wolf", "series": "Spice and Wolf",
            "series_index": 1, "book_type_id": types["light_novel"].id,
        }]})
        assert r.status_code == 200 and r.json()["accepted"], r.text
        assert (dirs["lib"] / "Light Novels/Spice and Wolf/Spice and Wolf v01.epub").is_file()

    def test_bindery_groups_preview_includes_type_folder(self, client, db, dirs, types, make_book):
        types["manga"].folder = "Manga"
        existing = make_book(title="Akira v1", author="Otomo", series="Akira", series_index=1.0)
        existing.book_type_id = types["manga"].id
        existing.is_reviewed = True
        db.commit()
        (dirs["inc"] / "Akira v02.cbz").write_bytes(b"PK\x03\x04fake")
        groups = client.get("/api/bindery/groups").json()
        previews = [f["dest_preview"] for g in groups for f in g["files"]]
        assert any(p.startswith("Manga/Akira/") for p in previews), previews

    def test_upload_detects_comic_before_move(self, client, db, dirs, types, monkeypatch):
        types["comic"].folder = "Comics"
        db.commit()
        monkeypatch.setattr("backend.api.books.extract_metadata",
                            lambda *a, **kw: {"title": "Saga", "series": "Saga", "series_index": 2.0})
        r = client.post("/api/books/upload", files={"file": ("saga2.cbz", b"cbz-upload", "application/zip")})
        assert r.status_code == 201, r.text
        bf = db.query(BookFile).filter(BookFile.book_id == r.json()["id"]).one()
        assert Path(bf.file_path) == (dirs["lib"] / "Comics/Saga/Saga v02.cbz").resolve()

    def test_upload_with_chosen_type(self, client, db, dirs, types, monkeypatch):
        types["book"].folder = "Books"
        db.commit()
        monkeypatch.setattr("backend.api.books.extract_metadata",
                            lambda *a, **kw: {"title": "Dune", "author": "Frank Herbert"})
        r = client.post("/api/books/upload", files={"file": ("dune.epub", b"epub-upload", "application/epub+zip")},
                        data={"book_type_id": str(types["book"].id)})
        assert r.status_code == 201, r.text
        assert (dirs["lib"] / "Books/Frank Herbert/Dune.epub").is_file()


# ── Existing books: misplaced, then reorganize ───────────────────────────────

def _placed_book(make_book, db, lib: Path, rel: str, **kw) -> Book:
    p = lib / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" + rel.encode())
    book = make_book(file_path=str(p), **kw)
    db.commit()
    return book


class TestMisplacedAndReorganize:
    def test_setting_folder_makes_existing_books_misplaced_then_reorganize(
        self, client, db, dirs, types, make_book
    ):
        lib = dirs["lib"]
        book = _placed_book(make_book, db, lib, "Saga/Saga v01.cbz",
                            title="Saga", author="Vaughan", series="Saga", series_index=1.0,
                            file_format="cbz")
        book.book_type_id = types["comic"].id
        db.commit()
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0

        # Setting the folder moves nothing...
        r = client.put(f"/api/book-types/{types['comic'].id}", json={"label": "Comics", "folder": "Comics"})
        assert r.status_code == 200
        assert (lib / "Saga/Saga v01.cbz").is_file()

        # ...the book is now misplaced
        health = client.get("/api/books/library-health").json()
        assert health["misplaced_count"] == 1
        issue = health["issues"][0]
        assert issue["current_path"] == "Saga/Saga v01.cbz"
        assert issue["expected_path"] == "Comics/Saga/Saga v01.cbz"

        # dry run lists, moves nothing
        fid = issue["file_id"]
        dry = client.post("/api/books/reorganize", json={"file_ids": [fid], "dry_run": True}).json()
        assert dry["moved"] == [{"file_id": fid, "from": "Saga/Saga v01.cbz", "to": "Comics/Saga/Saga v01.cbz"}]
        assert (lib / "Saga/Saga v01.cbz").is_file()

        # real run moves and updates the stored path
        real = client.post("/api/books/reorganize", json={"file_ids": [fid]}).json()
        assert real["moved"][0]["to"] == "Comics/Saga/Saga v01.cbz"
        assert (lib / "Comics/Saga/Saga v01.cbz").is_file()
        assert not (lib / "Saga").exists()
        db.expire_all()
        assert Path(db.get(BookFile, fid).file_path) == lib / "Comics/Saga/Saga v01.cbz"
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0

    def test_changing_book_type_makes_it_misplaced(self, client, db, dirs, types, make_book):
        types["manga"].folder = "Manga"
        db.commit()
        book = _placed_book(make_book, db, dirs["lib"], "Akira/Akira v01.cbz",
                            title="Akira", series="Akira", series_index=1.0, file_format="cbz")
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0
        r = client.put(f"/api/books/{book.id}", json={"book_type_id": types["manga"].id})
        assert r.status_code == 200, r.text
        assert (dirs["lib"] / "Akira/Akira v01.cbz").is_file()  # not moved
        health = client.get("/api/books/library-health").json()
        assert [i["expected_path"] for i in health["issues"]] == ["Manga/Akira/Akira v01.cbz"]


# ── Scan hint ────────────────────────────────────────────────────────────────

class TestScanHint:
    def test_folder_matching_type_assigns_type(self, db, dirs, types, monkeypatch):
        types["light_novel"].folder = "LN"
        db.flush()
        lib = dirs["lib"]
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {})
        for rel in ("manga/Frieren/Frieren v01.epub",   # label match, case-insensitive
                    "ln/Spice/Spice v01.epub",          # folder match, case-insensitive
                    "Frank Herbert/Dune.epub",          # no match
                    "Loose.epub"):                      # library root, no folder
            (lib / rel).parent.mkdir(parents=True, exist_ok=True)
            (lib / rel).write_bytes(rel.encode())
        res = scan_library(lib, dirs["covers"], db, workers=1)
        assert res.added == 4, res.error_details
        by_title = {b.title: b.book_type_id for b in db.query(Book).all()}
        assert by_title["Frieren v01"] == types["manga"].id
        assert by_title["Spice v01"] == types["light_novel"].id
        assert by_title["Dune"] is None
        assert by_title["Loose"] is None
        # The scan never moves files
        assert (lib / "manga/Frieren/Frieren v01.epub").is_file()

    def test_rule_wins_over_hint(self, db, dirs, types, monkeypatch):
        lib = dirs["lib"]
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {})
        (lib / "Manga/Saga").mkdir(parents=True)
        (lib / "Manga/Saga/Saga v01.cbz").write_bytes(b"cbz")
        scan_library(lib, dirs["covers"], db, workers=1)
        assert db.query(Book).one().book_type_id == types["comic"].id

    def test_folder_match_beats_label_match(self, db, dirs, types, monkeypatch):
        # "Comics" is the comic type's label but the book type's folder.
        types["book"].folder = "Comics"
        db.flush()
        lib = dirs["lib"]
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {})
        (lib / "Comics/X").mkdir(parents=True)
        (lib / "Comics/X/X.epub").write_bytes(b"epub")
        scan_library(lib, dirs["covers"], db, workers=1)
        assert db.query(Book).one().book_type_id == types["book"].id

    def test_hinted_book_in_its_folder_is_not_misplaced(self, client, db, dirs, types, monkeypatch):
        types["manga"].folder = "Manga"
        db.commit()
        lib = dirs["lib"]
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata",
                   {"Frieren v01.epub": {"title": "Frieren", "series": "Frieren", "series_index": 1.0}})
        (lib / "Manga/Frieren").mkdir(parents=True)
        (lib / "Manga/Frieren/Frieren v01.epub").write_bytes(b"epub")
        scan_library(lib, dirs["covers"], db, workers=1)
        db.commit()
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0
