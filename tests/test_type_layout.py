"""Library layout per book type: presets and an Advanced template (#276).

Covers:
- the three presets, with and without series / author / year
- series_first is byte-identical to the layout before #276 (frozen copy)
- template parser and renderer: fields, fallback, literal fallback, optional
  groups, sanitizing, and every validation error
- book-types API: layout fields stored and kept, 400 with the message
- layout-preview endpoint: real books, sample fallback, presets included
- placement uses the layout (import with author_series)
- a changed layout only marks books misplaced; dry run lists, reorganize
  moves and updates the stored path
- type folder and layout combined
"""
import itertools
import re
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.book import Book, BookFile
from backend.models.library import BookType
from backend.services.layout import (
    PRESETS,
    TemplateError,
    TypeLayout,
    first_author,
    parse_template,
    render_preset,
    render_template,
    validate_template,
)
from backend.services.organizer import get_library_path
from backend.services.scanner import import_incoming


# ── Frozen copy of the layout before #276 ────────────────────────────────────
# Do not "fix" this: it is the reference series_first must keep matching.

_O_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_O_SPACES = re.compile(r'\s+')
_O_TRAIL = re.compile(r'[.\s]+$')
_O_VOL = re.compile(r',?\s*\b(?:vol\.?|volume|v)\s*\d+(?:\.\d+)?', re.IGNORECASE)


def _o_sanitize(s):
    s = _O_UNSAFE.sub('', s)
    s = _O_SPACES.sub(' ', s).strip()
    s = _O_TRAIL.sub('', s)
    return s or 'Unknown'


def _o_vol(index):
    if index is None:
        return ''
    if index == int(index):
        return f'v{int(index):02d}'
    return f'v{index:.1f}'


def _o_strip(title):
    return _O_VOL.sub('', title).strip().rstrip(',- ').strip()


def _old_layout_path(meta, original_filename):
    suffix = Path(original_filename).suffix.lower() or '.epub'
    title = _o_sanitize(meta.get('title') or Path(original_filename).stem)
    series = meta.get('series')
    author = meta.get('author')
    year = meta.get('year')
    idx = meta.get('series_index')
    if series:
        folder = _o_sanitize(series)
        clean_title = _o_strip(title)
        vol = _o_vol(idx)
        filename = f'{clean_title} {vol}{suffix}' if vol else f'{clean_title}{suffix}'
        return Path(folder) / filename
    if author:
        folder = _o_sanitize(author)
        yr_part = f' ({year})' if year else ''
        filename = f'{title}{yr_part}{suffix}'
        return Path(folder) / filename
    return Path('Unknown') / (title + suffix)


TITLES = ["Dune", "Akira, Vol. 3", "Frieren v02", "What? Why: How*", "  spaced   out  ",
          "Ending dots...", "Ünïcödé 日本語", "", None, "a/b\\c", "Volume 4"]
SERIES = [None, "", "Akira", "Saga: Part <1>", "Ränder", "..."]
INDEXES = [None, 1.0, 10.5, 0.0, 123.0]
AUTHORS = [None, "", "Frank Herbert", "Herbert, Frank", "A B, C D", "Le Guin; Ursula", "Ö/Ä"]
YEARS = [None, 1965, 0]
FILES = ["book.EPUB", "x.cbz", "noext", "a.b.pdf"]


def _matrix():
    for title, series, idx, author, year, fn in itertools.product(
        TITLES, SERIES, INDEXES, AUTHORS, YEARS, FILES
    ):
        yield {"title": title, "series": series, "series_index": idx,
               "author": author, "year": year}, fn


class TestSeriesFirstUnchanged:
    def test_identical_to_old_layout_over_matrix(self):
        n = 0
        for meta, fn in _matrix():
            assert render_preset("series_first", meta, fn) == _old_layout_path(meta, fn), (meta, fn)
            assert get_library_path(meta, fn) == _old_layout_path(meta, fn)
            assert get_library_path(meta, fn, TypeLayout()) == _old_layout_path(meta, fn)
            n += 1
        assert n > 10000

    def test_default_layout_with_folder(self):
        meta = {"title": "Saga", "series": "Saga", "series_index": 1.0}
        assert get_library_path(meta, "s.cbz", TypeLayout(folder="Comics")) == Path("Comics/Saga/Saga v01.cbz")
        assert get_library_path(meta, "s.cbz", "Comics") == Path("Comics/Saga/Saga v01.cbz")


# ── Presets ──────────────────────────────────────────────────────────────────

SERIES_BOOK = {"title": "Deal Breaker", "author": "Harlan Coben", "series": "Myron Bolitar",
               "series_index": 1.0, "year": 1995}
SERIES_BOOK_2 = {"title": "Shelter", "author": "Harlan Coben", "series": "Mickey Bolitar",
                 "series_index": 10.5}
STANDALONE = {"title": "Tell No One", "author": "Harlan Coben", "year": 2001}
NO_YEAR = {"title": "Meditations", "author": "Marcus Aurelius"}
NO_AUTHOR_SERIES = {"title": "Saga v01", "series": "Saga", "series_index": 1.0}
NOTHING = {"title": "Nameless"}


@pytest.mark.parametrize("preset,meta,expected", [
    ("series_first", SERIES_BOOK, "Myron Bolitar/Deal Breaker v01.epub"),
    ("series_first", STANDALONE, "Harlan Coben/Tell No One (2001).epub"),
    ("series_first", NO_YEAR, "Marcus Aurelius/Meditations.epub"),
    ("series_first", NOTHING, "Unknown/Nameless.epub"),
    ("author_series", SERIES_BOOK, "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub"),
    ("author_series", SERIES_BOOK_2, "Harlan Coben/Mickey Bolitar/Shelter v10.5.epub"),
    ("author_series", STANDALONE, "Harlan Coben/Tell No One (2001).epub"),
    ("author_series", NO_YEAR, "Marcus Aurelius/Meditations.epub"),
    ("author_series", NO_AUTHOR_SERIES, "Unknown/Saga/Saga v01.epub"),
    ("author_series", NOTHING, "Unknown/Nameless.epub"),
    ("author_series", {"title": "Saga", "series": "Saga"}, "Unknown/Saga/Saga.epub"),
    ("author_only", SERIES_BOOK, "Harlan Coben/Deal Breaker v01.epub"),
    ("author_only", SERIES_BOOK_2, "Harlan Coben/Shelter v10.5.epub"),
    ("author_only", STANDALONE, "Harlan Coben/Tell No One (2001).epub"),
    ("author_only", NO_YEAR, "Marcus Aurelius/Meditations.epub"),
    ("author_only", NO_AUTHOR_SERIES, "Unknown/Saga v01.epub"),
    ("author_only", NOTHING, "Unknown/Nameless.epub"),
])
def test_presets(preset, meta, expected):
    assert str(render_preset(preset, meta, "file.epub")) == expected


def test_author_presets_use_first_author():
    meta = dict(SERIES_BOOK, author="Harlan Coben, Stephen King")
    assert str(render_preset("author_series", meta, "a.epub")) == "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub"
    # series_first keeps the whole author string, as before
    assert str(render_preset("series_first", dict(STANDALONE, author="Harlan Coben, Stephen King"), "a.epub")) \
        == "Harlan Coben, Stephen King/Tell No One (2001).epub"


@pytest.mark.parametrize("raw,first", [
    ("Harlan Coben", "Harlan Coben"),
    ("Harlan Coben, Stephen King", "Harlan Coben"),
    ("Coben, Harlan", "Coben, Harlan"),          # one author, last-name-first
    ("Neil Gaiman & Terry Pratchett", "Neil Gaiman"),
    ("Gaiman; Pratchett", "Gaiman"),
    ("", ""),
    (None, ""),
])
def test_first_author(raw, first):
    assert first_author(raw) == first


def test_unknown_preset_falls_back_to_series_first():
    assert render_preset("nope", SERIES_BOOK, "a.epub") == _old_layout_path(SERIES_BOOK, "a.epub")


def test_preset_paths_never_escape():
    for preset in PRESETS:
        for meta, fn in _matrix():
            rel = render_preset(preset, meta, fn)
            assert not rel.is_absolute()
            assert ".." not in rel.parts and "" not in rel.parts


# ── Template ─────────────────────────────────────────────────────────────────

class TestTemplate:
    def r(self, t, meta, fn="f.epub"):
        return str(render_template(t, meta, fn))

    def test_fields(self):
        t = "{author}/{series}/{title} - {index} - {year}{ext}"
        assert self.r(t, SERIES_BOOK) == "Harlan Coben/Myron Bolitar/Deal Breaker - 01 - 1995.epub"
        assert self.r("{title} {index}{ext}", SERIES_BOOK_2, "x.CBZ") == "Shelter 10.5.cbz"

    def test_title_strips_volume_only_for_series(self):
        assert self.r("{title}{ext}", {"title": "Akira, Vol. 3", "series": "Akira", "series_index": 3.0}) == "Akira.epub"
        assert self.r("{title}{ext}", {"title": "Akira, Vol. 3"}) == "Akira, Vol. 3.epub"

    def test_fallback(self):
        t = "{series|author}/{title}{ext}"
        assert self.r(t, SERIES_BOOK) == "Myron Bolitar/Deal Breaker.epub"
        assert self.r(t, STANDALONE) == "Harlan Coben/Tell No One.epub"
        # nothing to fall back on: empty component becomes Unknown
        assert self.r(t, NOTHING) == "Unknown/Nameless.epub"

    def test_literal_fallback(self):
        t = "{series|author|No author}/{title}{ext}"
        assert self.r(t, NOTHING) == "No author/Nameless.epub"
        assert self.r(t, STANDALONE) == "Harlan Coben/Tell No One.epub"

    def test_optional_group(self):
        t = "{author}/{title}[ ({year})]{ext}"
        assert self.r(t, STANDALONE) == "Harlan Coben/Tell No One (2001).epub"
        assert self.r(t, NO_YEAR) == "Marcus Aurelius/Meditations.epub"
        t = "{title}[ v{index}][ ({year})]{ext}"
        assert self.r(t, SERIES_BOOK) == "Deal Breaker v01 (1995).epub"
        assert self.r(t, SERIES_BOOK_2) == "Shelter v10.5.epub"

    def test_group_needs_every_field(self):
        assert self.r("{title}[ {series} {year}]{ext}", SERIES_BOOK_2) == "Shelter.epub"

    def test_group_with_folder(self):
        t = "{author|Unknown}/[{series}/]{title}[ v{index}]{ext}"
        assert self.r(t, SERIES_BOOK) == "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub"
        assert self.r(t, STANDALONE) == "Harlan Coben/Tell No One.epub"

    def test_sanitizes_every_component(self):
        meta = {"title": 'Who? What: "Why"', "author": "AC/DC", "series": "../../etc", "series_index": 1.0}
        rel = render_template("{author}/{series}/{title}{ext}", meta, "a.epub")
        # same sanitizing as the default layout: separators removed, no escape
        assert str(rel) == "ACDC/....etc/Who What Why.epub"
        assert ".." not in rel.parts and len(rel.parts) == 3

    def test_dot_values_cannot_escape(self):
        rel = render_template("{series}/{title}{ext}", {"title": "x", "series": ".."}, "a.epub")
        assert rel.parts[0] == "Unknown"

    def test_preset_templates_are_valid_and_close(self):
        for name, info in PRESETS.items():
            assert validate_template(info["template"]) is None, name
            # identical for series books
            assert render_template(info["template"], SERIES_BOOK, "a.epub") == render_preset(name, SERIES_BOOK, "a.epub")

    @pytest.mark.parametrize("bad,needle", [
        ("{foo}/{title}{ext}", "Unknown field {foo} at position 1"),
        ("[{title}{ext}", "Unclosed [ at position 1"),
        ("{title{ext}", "Unclosed { at position 1"),
        ("{title}]{ext}", "Unexpected ] at position 8"),
        ("{title}}{ext}", "Unexpected } at position 8"),
        ("[a[{title}]]{ext}", "cannot be nested at position 3"),
        ("{title}", "must end with {ext}"),
        ("{title}{ext}.epub", "must end with {ext}"),
        ("{title}[{ext}]", "must end with {ext}"),
        ("../{title}{ext}", ". or .. are not allowed at position 1"),
        ("{author}/../{title}{ext}", "at position 10"),
        ("/{title}{ext}", "must not start with /"),
        ("{author}//{title}{ext}", "Empty folder name at position 10"),
        ("{author}/{ext}", "needs a field outside optional groups"),
        ("{author}/[{title}]{ext}", "needs a field outside optional groups"),
        ("{title}[ x]{ext}", "needs at least one field"),
        ("{}{ext}", "Empty field name"),
        ("{title|Unknown|author}{ext}", "only allowed as the last fallback"),
        ("{title}\\{ext}", "not \\"),
        ("", "empty"),
        ("{title}" * 40 + "{ext}", "at most 255"),
    ])
    def test_invalid(self, bad, needle):
        err = validate_template(bad)
        assert err and needle in err, err
        with pytest.raises(TemplateError):
            parse_template(bad)

    def test_broken_stored_template_falls_back_to_preset(self):
        lay = TypeLayout(preset="author_only", template="{nope}")
        assert get_library_path(SERIES_BOOK, "a.epub", lay) == Path("Harlan Coben/Deal Breaker v01.epub")

    def test_template_beats_preset_and_folder_prefixes(self):
        lay = TypeLayout(folder="Books", preset="author_only", template="{title}{ext}")
        assert get_library_path(SERIES_BOOK, "a.epub", lay) == Path("Books/Deal Breaker.epub")


# ── Fixtures for the API / placement tests ───────────────────────────────────

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
    for i, (slug, label) in enumerate([("book", "Books"), ("manga", "Manga"), ("comic", "Comics")]):
        bt = BookType(slug=slug, label=label, sort_order=i)
        db.add(bt)
        out[slug] = bt
    db.flush()
    return out


def _placed_book(make_book, db, lib: Path, rel: str, **kw) -> Book:
    p = lib / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" + rel.encode())
    book = make_book(file_path=str(p), **kw)
    db.commit()
    return book


# ── API ──────────────────────────────────────────────────────────────────────

class TestApi:
    def test_defaults_listed(self, client: TestClient, types, db):
        db.commit()
        rows = client.get("/api/book-types").json()
        assert {(r["layout_preset"], r["layout_template"]) for r in rows} == {("series_first", "")}

    def test_seed_defaults(self, db: Session):
        from backend.services.book_types import seed_book_types
        if db.query(BookType).count() == 0:
            seed_book_types(db)
        assert {(bt.layout_preset, bt.layout_template) for bt in db.query(BookType).all()} == {("series_first", "")}

    def test_create_with_layout(self, client: TestClient):
        r = client.post("/api/book-types", json={"label": "Novels", "layout_preset": "author_series"})
        assert r.status_code == 201, r.text
        assert r.json()["layout_preset"] == "author_series"
        assert r.json()["layout_template"] == ""

    def test_update_sets_keeps_and_clears(self, client: TestClient, types, db):
        db.commit()
        bid = types["book"].id
        r = client.put(f"/api/book-types/{bid}", json={"label": "Books", "layout_preset": "author_only",
                                                        "layout_template": "{author}/{title}{ext}"})
        assert r.status_code == 200, r.text
        assert (r.json()["layout_preset"], r.json()["layout_template"]) == ("author_only", "{author}/{title}{ext}")
        # omitted fields keep the stored values
        r = client.put(f"/api/book-types/{bid}", json={"label": "Books 2"})
        assert (r.json()["layout_preset"], r.json()["layout_template"]) == ("author_only", "{author}/{title}{ext}")
        # "" turns Advanced off, back to the preset
        r = client.put(f"/api/book-types/{bid}", json={"label": "Books", "layout_template": ""})
        assert (r.json()["layout_preset"], r.json()["layout_template"]) == ("author_only", "")

    @pytest.mark.parametrize("body,needle", [
        ({"layout_template": "{foo}{ext}"}, "Unknown field {foo}"),
        ({"layout_template": "[{title}{ext}"}, "Unclosed ["),
        ({"layout_template": "{title}"}, "must end with {ext}"),
        ({"layout_template": "../{title}{ext}"}, ". or .."),
        ({"layout_preset": "by_mood"}, "Unknown layout preset"),
    ])
    def test_invalid_is_400_and_changes_nothing(self, client: TestClient, types, db, body, needle):
        db.commit()
        bid = types["book"].id
        r = client.put(f"/api/book-types/{bid}", json={"label": "Renamed", "folder": "X", **body})
        assert r.status_code == 400
        assert needle in r.json()["detail"]
        db.expire_all()
        bt = db.get(BookType, bid)
        assert (bt.label, bt.folder, bt.layout_preset, bt.layout_template) == ("Books", "", "series_first", "")
        assert client.post("/api/book-types", json={"label": "New", **body}).status_code == 400

    def test_preview_requires_admin(self, client: TestClient, db, admin_user):
        user, _ = admin_user
        user.role = "member"
        user.is_admin = False
        db.commit()
        assert client.post("/api/book-types/layout-preview", json={}).status_code == 403


class TestPreview:
    def test_sample_books_when_library_empty(self, client: TestClient, types, db):
        db.commit()
        r = client.post("/api/book-types/layout-preview",
                        json={"preset": "author_series", "book_type_id": types["book"].id})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["source"] == "sample" and j["error"] is None
        assert j["paths"][0] == {"title": "Deal Breaker", "path": "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub"}
        assert set(j["presets"]) == {"series_first", "author_series", "author_only"}
        assert j["presets"]["series_first"]["paths"][0]["path"] == "Myron Bolitar/Deal Breaker v01.epub"
        assert j["presets"]["author_only"]["template"] == PRESETS["author_only"]["template"]
        assert j["presets"]["author_only"]["label"]

    def test_real_books_of_the_type_with_a_mix(self, client: TestClient, types, db, dirs, make_book):
        types["comic"].folder = "Comics"
        for i in range(1, 9):
            b = _placed_book(make_book, db, dirs["lib"], f"Saga/Saga v{i:02d}.cbz", title="Saga",
                             author="Brian K. Vaughan", series="Saga", series_index=float(i), file_format="cbz")
            b.book_type_id = types["comic"].id
        b = _placed_book(make_book, db, dirs["lib"], "Moore/Watchmen (1987).cbz", title="Watchmen",
                         author="Alan Moore", year=1987, file_format="cbz")
        b.book_type_id = types["comic"].id
        b = _placed_book(make_book, db, dirs["lib"], "Other/Novel.epub", title="Novel", author="X")
        db.commit()
        j = client.post("/api/book-types/layout-preview",
                        json={"preset": "author_series", "book_type_id": types["comic"].id}).json()
        assert j["source"] == "type"
        paths = [p["path"] for p in j["paths"]]
        assert len(paths) == 6
        assert "Comics/Brian K. Vaughan/Saga/Saga v01.cbz" in paths
        assert "Comics/Alan Moore/Watchmen (1987).cbz" in paths
        assert not any("Novel" in p for p in paths)
        # the form's unsaved folder wins over the stored one
        j = client.post("/api/book-types/layout-preview",
                        json={"preset": "series_first", "book_type_id": types["comic"].id, "folder": ""}).json()
        assert "Saga/Saga v01.cbz" in [p["path"] for p in j["paths"]]

    def test_falls_back_to_any_books(self, client: TestClient, types, db, dirs, make_book):
        _placed_book(make_book, db, dirs["lib"], "X/Dune.epub", title="Dune", author="Frank Herbert", year=1965)
        j = client.post("/api/book-types/layout-preview",
                        json={"preset": "author_only", "book_type_id": types["manga"].id}).json()
        assert j["source"] == "library"
        assert j["paths"] == [{"title": "Dune", "path": "Frank Herbert/Dune (1965).epub"}]

    def test_template_and_error(self, client: TestClient, types, db):
        db.commit()
        j = client.post("/api/book-types/layout-preview",
                        json={"template": "{author}/{title}[ ({year})]{ext}"}).json()
        assert j["error"] is None
        assert j["paths"][0]["path"] == "Harlan Coben/Deal Breaker (1995).epub"
        assert j["paths"][2]["path"] == "Marcus Aurelius/Meditations.epub"
        j = client.post("/api/book-types/layout-preview", json={"template": "{foo}{ext}"}).json()
        assert j["paths"] == [] and "Unknown field {foo}" in j["error"]
        assert j["presets"]["series_first"]["paths"]  # examples still shown


# ── Placement ────────────────────────────────────────────────────────────────

def _fake_meta(monkeypatch, target: str, by_name: dict[str, dict]):
    def fake(path, *a, **kw):
        return dict(by_name.get(Path(path).name, {"title": Path(path).stem}))
    monkeypatch.setattr(target, fake)


class TestPlacement:
    def test_import_with_author_series(self, db, dirs, types, monkeypatch):
        types["book"].layout_preset = "author_series"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {
            "db.epub": {"title": "Deal Breaker", "author": "Harlan Coben", "series": "Myron Bolitar",
                        "series_index": 1.0},
            "tno.epub": {"title": "Tell No One", "author": "Harlan Coben", "year": 2001},
        })
        (dirs["inc"] / "db.epub").write_bytes(b"epub-1")
        (dirs["inc"] / "tno.epub").write_bytes(b"epub-2")
        res = import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db, default_type_id=types["book"].id)
        assert res.added == 2, res.error_details
        assert (dirs["lib"] / "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub").is_file()
        assert (dirs["lib"] / "Harlan Coben/Tell No One (2001).epub").is_file()

    def test_import_folder_and_template(self, db, dirs, types, monkeypatch):
        types["comic"].folder = "Comics"
        types["comic"].layout_template = "{series|author|Unknown}/{title}[ #{index}]{ext}"
        db.flush()
        _fake_meta(monkeypatch, "backend.services.scanner.extract_metadata", {
            "saga.cbz": {"title": "Saga", "series": "Saga", "series_index": 3.0},
        })
        (dirs["inc"] / "saga.cbz").write_bytes(b"cbz")
        import_incoming(dirs["inc"], dirs["lib"], dirs["covers"], db)
        assert (dirs["lib"] / "Comics/Saga/Saga #03.cbz").is_file()

    def test_bindery_accept_uses_layout(self, client, db, dirs, types, monkeypatch):
        types["book"].layout_preset = "author_only"
        db.commit()
        monkeypatch.setattr("backend.api.bindery.extract_metadata", lambda *a, **kw: {"title": "x"})
        (dirs["inc"] / "x.epub").write_bytes(b"epub")
        r = client.post("/api/bindery/accept", json={"files": [{
            "path": "x.epub", "title": "Deal Breaker", "author": "Harlan Coben", "series": "Myron Bolitar",
            "series_index": 1, "book_type_id": types["book"].id,
        }]})
        assert r.status_code == 200 and r.json()["accepted"], r.text
        assert (dirs["lib"] / "Harlan Coben/Deal Breaker v01.epub").is_file()

    def test_upload_uses_layout(self, client, db, dirs, types, monkeypatch):
        types["book"].layout_preset = "author_series"
        db.commit()
        monkeypatch.setattr("backend.api.books.extract_metadata",
                            lambda *a, **kw: dict(SERIES_BOOK))
        r = client.post("/api/books/upload", files={"file": ("d.epub", b"epub-up", "application/epub+zip")},
                        data={"book_type_id": str(types["book"].id)})
        assert r.status_code == 201, r.text
        assert (dirs["lib"] / "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub").is_file()


# ── Changed layout: misplaced, then reorganize ───────────────────────────────

class TestMisplacedAndReorganize:
    def test_preset_change_marks_misplaced_then_reorganize(self, client, db, dirs, types, make_book):
        lib = dirs["lib"]
        a = _placed_book(make_book, db, lib, "Myron Bolitar/Deal Breaker v01.epub", title="Deal Breaker",
                         author="Harlan Coben", series="Myron Bolitar", series_index=1.0)
        b = _placed_book(make_book, db, lib, "Mickey Bolitar/Shelter v01.epub", title="Shelter",
                         author="Harlan Coben", series="Mickey Bolitar", series_index=1.0)
        c = _placed_book(make_book, db, lib, "Harlan Coben/Tell No One (2001).epub", title="Tell No One",
                         author="Harlan Coben", year=2001)
        for bk in (a, b, c):
            bk.book_type_id = types["book"].id
        db.commit()
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0

        r = client.put(f"/api/book-types/{types['book'].id}", json={"label": "Books", "layout_preset": "author_series"})
        assert r.status_code == 200, r.text
        # saving moved nothing
        assert (lib / "Myron Bolitar/Deal Breaker v01.epub").is_file()

        health = client.get("/api/books/library-health").json()
        assert health["misplaced_count"] == 2
        expected = {i["current_path"]: i["expected_path"] for i in health["issues"]}
        assert expected == {
            "Myron Bolitar/Deal Breaker v01.epub": "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub",
            "Mickey Bolitar/Shelter v01.epub": "Harlan Coben/Mickey Bolitar/Shelter v01.epub",
        }
        fids = [i["file_id"] for i in health["issues"]]
        dry = client.post("/api/books/reorganize", json={"file_ids": fids, "dry_run": True}).json()
        assert {m["to"] for m in dry["moved"]} == set(expected.values())
        assert (lib / "Myron Bolitar/Deal Breaker v01.epub").is_file()

        real = client.post("/api/books/reorganize", json={"file_ids": fids}).json()
        assert len(real["moved"]) == 2 and not real["errors"]
        assert (lib / "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub").is_file()
        assert (lib / "Harlan Coben/Mickey Bolitar/Shelter v01.epub").is_file()
        assert not (lib / "Myron Bolitar").exists()
        db.expire_all()
        paths = {Path(bf.file_path) for bf in db.query(BookFile).all()}
        assert lib / "Harlan Coben/Myron Bolitar/Deal Breaker v01.epub" in paths
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0

    def test_template_and_folder_change(self, client, db, dirs, types, make_book):
        lib = dirs["lib"]
        bk = _placed_book(make_book, db, lib, "Saga/Saga v01.cbz", title="Saga", author="Brian K. Vaughan",
                          series="Saga", series_index=1.0, year=2012, file_format="cbz")
        bk.book_type_id = types["comic"].id
        db.commit()
        r = client.put(f"/api/book-types/{types['comic'].id}",
                       json={"label": "Comics", "folder": "Comics", "layout_template": "{author}/{title}[ ({year})]{ext}"})
        assert r.status_code == 200, r.text
        health = client.get("/api/books/library-health").json()
        assert [i["expected_path"] for i in health["issues"]] == ["Comics/Brian K. Vaughan/Saga (2012).cbz"]
        fid = health["issues"][0]["file_id"]
        real = client.post("/api/books/reorganize", json={"file_ids": [fid]}).json()
        assert real["moved"][0]["to"] == "Comics/Brian K. Vaughan/Saga (2012).cbz"
        assert (lib / "Comics/Brian K. Vaughan/Saga (2012).cbz").is_file()
        assert client.get("/api/books/library-health").json()["misplaced_count"] == 0
