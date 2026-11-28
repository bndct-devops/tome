"""The download embed and the book's cover (issue #344).

Tome's stored cover is normally the file's own cover, scaled to 600 px and
saved as JPEG. Writing that back into a download made the file worse: an EPUB
lost its full-size cover, a comic got its first page twice. The embed now
touches the cover only when Tome shows a different picture, and then writes
it in a form that matches the name and type it is stored under.
"""
import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from backend.services.metadata import save_cover
from backend.services.metadata_embed import (
    COVER_NAME, OPF_NS, _cover_changed, _embed_cbz, _embed_epub,
)


def _book() -> SimpleNamespace:
    return SimpleNamespace(
        title="A Book", subtitle=None, author="An Author", publisher=None,
        description=None, language=None, year=None, isbn=None,
        series=None, series_index=None, tags=[],
    )


def _picture(seed: int, size: tuple[int, int] = (1200, 1800), fmt: str = "JPEG") -> bytes:
    """A cover-like picture: a gradient with blocks and a line. Different
    seeds give clearly different pictures; ``size`` scales the same picture."""
    w, h = 1200, 1800
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, 8):
        shade = int(255 * y / h)
        draw.rectangle(
            [0, y, w, y + 8],
            fill=((shade + seed * 70) % 256, (seed * 110) % 256, (255 - shade + seed * 30) % 256),
        )
    for i in range(6):
        x0 = (seed * 137 + i * 211) % (w - w // 4)
        y0 = (seed * 89 + i * 307) % (h - h // 5)
        draw.rectangle(
            [x0, y0, x0 + w // 4, y0 + h // 5],
            fill=((i * 50 + seed * 40) % 256, (i * 90) % 256, (seed * 60 + 30) % 256),
        )
    draw.line([0, 0, w, h], fill=(255, 255, 255), width=12)
    if size != (w, h):
        img = img.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


def _stored(own: bytes, tmp_path: Path) -> bytes:
    """The cover Tome stores for a file whose own cover is ``own``."""
    name = save_cover(own, tmp_path, "a" * 64)
    assert name
    return (tmp_path / name).read_bytes()


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _read(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _format(data: bytes) -> str:
    return Image.open(io.BytesIO(data)).format


class TestCoverChanged:
    def test_stored_copy_of_the_files_cover_is_not_a_change(self, tmp_path):
        for fmt in ("JPEG", "PNG"):
            own = _picture(1, (1600, 2400), fmt)
            assert _cover_changed(own, _stored(own, tmp_path)) is False

    def test_small_cover_stored_unscaled_is_not_a_change(self, tmp_path):
        own = _picture(2, (400, 600))
        assert _cover_changed(own, _stored(own, tmp_path)) is False

    def test_another_picture_is_a_change(self, tmp_path):
        assert _cover_changed(_picture(1), _stored(_picture(2), tmp_path)) is True

    def test_another_edition_is_a_change(self):
        # Same artwork with other lettering: a band of another colour
        img = Image.open(io.BytesIO(_picture(1)))
        ImageDraw.Draw(img).rectangle([0, 200, 1200, 700], fill=(250, 240, 60))
        out = io.BytesIO()
        img.save(out, "JPEG")
        assert _cover_changed(_picture(1), out.getvalue()) is True

    def test_same_picture_other_shape_is_a_change(self):
        assert _cover_changed(_picture(1, (1200, 1800)), _picture(1, (1200, 1200))) is True

    def test_smaller_scan_of_the_same_picture_is_not_a_change(self):
        # A fetched cover of the same artwork: the file's own is the better one.
        own = _picture(1, (1200, 1800))
        assert _cover_changed(own, _picture(1, (354, 531))) is False
        assert _cover_changed(own, _picture(1, (354, 531)), allow_upgrade=True) is False

    def test_larger_scan_of_the_same_picture_only_counts_as_an_upgrade(self):
        own = _picture(1, (400, 600))
        assert _cover_changed(own, _picture(1, (1200, 1800))) is False
        assert _cover_changed(own, _picture(1, (1200, 1800)), allow_upgrade=True) is True
        assert _cover_changed(own, _picture(1, (440, 660)), allow_upgrade=True) is False

    def test_brighter_or_duller_scan_of_the_same_picture_is_not_a_change(self):
        from PIL import ImageEnhance
        own = _picture(1)
        img = Image.open(io.BytesIO(own))
        for scan in (
            ImageEnhance.Brightness(img).enhance(1.12),
            ImageEnhance.Color(ImageEnhance.Brightness(img).enhance(0.9)).enhance(1.25),
            ImageEnhance.Contrast(img).enhance(1.15),
        ):
            out = io.BytesIO()
            scan.resize((400, 600)).save(out, "JPEG", quality=70)
            assert _cover_changed(own, out.getvalue()) is False

    def test_identical_bytes_are_not_a_change(self):
        own = _picture(1, (1200, 1800))
        assert _cover_changed(own, own, allow_upgrade=True) is False

    def test_file_without_a_cover_is_a_change(self):
        assert _cover_changed(None, _picture(1)) is True
        assert _cover_changed(b"not an image", _picture(1)) is True

    def test_unreadable_tome_cover_is_never_written(self):
        assert _cover_changed(_picture(1), b"not an image") is False
        assert _cover_changed(None, b"not an image") is False


class TestCbz:
    def _comic(self, tmp_path) -> tuple[Path, dict[str, bytes]]:
        pages = {f"00{i}.jpg": _picture(i) for i in (1, 2, 3)}
        return _zip(tmp_path / "c.cbz", pages), pages

    def test_untouched_cover_adds_no_page(self, tmp_path):
        src, pages = self._comic(tmp_path)
        out = _read(_embed_cbz(_book(), src, _stored(pages["001.jpg"], tmp_path)))
        assert sorted(out) == ["001.jpg", "002.jpg", "003.jpg", "ComicInfo.xml"]
        for name, data in pages.items():
            assert out[name] == data

    def test_fetched_scan_of_the_first_page_adds_no_page(self, tmp_path):
        src, _ = self._comic(tmp_path)
        out = _read(_embed_cbz(_book(), src, _picture(1, (354, 531))))
        assert COVER_NAME not in out

    def test_no_tome_cover_adds_no_page(self, tmp_path):
        src, _ = self._comic(tmp_path)
        assert COVER_NAME not in _read(_embed_cbz(_book(), src, None))

    def test_picked_cover_goes_in_front(self, tmp_path):
        src, pages = self._comic(tmp_path)
        picked = _picture(9, (800, 1200))
        out = _embed_cbz(_book(), src, picked)
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            assert zf.namelist()[0] == COVER_NAME
        files = _read(out)
        assert files[COVER_NAME] == picked
        for name, data in pages.items():
            assert files[name] == data

    def test_picked_cover_in_another_format_is_written_as_jpeg(self, tmp_path):
        # Covers set in Tome are stored as uploaded, whatever the type.
        src, _ = self._comic(tmp_path)
        out = _read(_embed_cbz(_book(), src, _picture(9, (800, 1200), "PNG")))
        assert _format(out[COVER_NAME]) == "JPEG"

    def test_reimported_download_keeps_its_cover_page(self, tmp_path):
        picked = _picture(9, (800, 1200))
        src = _zip(tmp_path / "r.cbz", {COVER_NAME: picked, "001.jpg": _picture(1)})
        out = _read(_embed_cbz(_book(), src, _stored(picked, tmp_path)))
        assert out[COVER_NAME] == picked


CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    '</rootfiles></container>'
).encode()


def _opf(manifest: str, version: str = "3.0", meta: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="uid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">'
        f'<dc:title>Old</dc:title>{meta}</metadata>'
        f'<manifest><item id="t" href="t.xhtml" media-type="application/xhtml+xml"/>{manifest}</manifest>'
        '<spine><itemref idref="t"/></spine></package>'
    ).encode()


def _epub(tmp_path: Path, opf: bytes, images: dict[str, bytes]) -> Path:
    entries = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": CONTAINER,
        "OEBPS/content.opf": opf,
        "OEBPS/t.xhtml": b"<html/>",
    }
    entries.update(images)
    return _zip(tmp_path / "b.epub", entries)


def _items(opf: bytes) -> dict[str, dict]:
    root = ET.fromstring(opf)
    return {i.get("id"): dict(i.attrib) for i in root.iter(f"{{{OPF_NS}}}item")}


def _cover_meta(opf: bytes) -> list[str]:
    root = ET.fromstring(opf)
    return [m.get("content") for m in root.iter(f"{{{OPF_NS}}}meta") if m.get("name") == "cover"]


PNG_COVER = '<item id="cov" href="images/cover.png" media-type="image/png" properties="cover-image"/>'
JPG_COVER = '<item id="cov" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>'


class TestEpub:
    def test_untouched_cover_stays_as_it_is(self, tmp_path):
        own = _picture(1, (1600, 2400), "PNG")
        src = _epub(tmp_path, _opf(PNG_COVER), {"OEBPS/images/cover.png": own})
        out = _read(_embed_epub(_book(), src, _stored(own, tmp_path)))
        assert out["OEBPS/images/cover.png"] == own
        assert _items(out["OEBPS/content.opf"])["cov"]["media-type"] == "image/png"

    def test_untouched_epub2_cover_stays_as_it_is(self, tmp_path):
        own = _picture(1, (1600, 2400))
        opf = _opf('<item id="cov" href="images/cover.jpg" media-type="image/jpeg"/>',
                   version="2.0", meta='<meta name="cover" content="cov"/>')
        src = _epub(tmp_path, opf, {"OEBPS/images/cover.jpg": own})
        out = _read(_embed_epub(_book(), src, _stored(own, tmp_path)))
        assert out["OEBPS/images/cover.jpg"] == own

    def test_picked_cover_replaces_it_in_the_files_own_format(self, tmp_path):
        own = _picture(1, (1600, 2400), "PNG")
        picked = _picture(9, (800, 1200))  # a JPEG
        src = _epub(tmp_path, _opf(PNG_COVER), {"OEBPS/images/cover.png": own})
        out = _read(_embed_epub(_book(), src, picked))
        new = out["OEBPS/images/cover.png"]
        assert _format(new) == "PNG"
        assert _cover_changed(picked, new) is False  # the picked picture
        assert _items(out["OEBPS/content.opf"])["cov"]["media-type"] == "image/png"

    def test_larger_scan_of_the_same_cover_replaces_a_small_one(self, tmp_path):
        better = _picture(1, (1200, 1800))
        src = _epub(tmp_path, _opf(JPG_COVER), {"OEBPS/images/cover.jpg": _picture(1, (300, 450))})
        out = _read(_embed_epub(_book(), src, better))
        assert out["OEBPS/images/cover.jpg"] == better

    def test_fetched_scan_of_the_same_cover_leaves_the_larger_one(self, tmp_path):
        own = _picture(1, (1600, 2400), "PNG")
        src = _epub(tmp_path, _opf(PNG_COVER), {"OEBPS/images/cover.png": own})
        out = _read(_embed_epub(_book(), src, _picture(1, (354, 531))))
        assert out["OEBPS/images/cover.png"] == own

    def test_picked_cover_of_the_same_type_is_written_as_is(self, tmp_path):
        picked = _picture(9, (800, 1200))
        src = _epub(tmp_path, _opf(JPG_COVER), {"OEBPS/images/cover.jpg": _picture(1)})
        out = _read(_embed_epub(_book(), src, picked))
        assert out["OEBPS/images/cover.jpg"] == picked

    def test_cover_type_tome_cannot_write_is_left_alone(self, tmp_path):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        opf = _opf('<item id="cov" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>')
        src = _epub(tmp_path, opf, {"OEBPS/cover.svg": svg})
        out = _read(_embed_epub(_book(), src, _picture(9)))
        assert out["OEBPS/cover.svg"] == svg

    def test_no_named_cover_and_unchanged_adds_nothing(self, tmp_path):
        # Tome's cover came from the first image; nothing to do.
        first = _picture(1)
        opf = _opf('<item id="i1" href="images/p1.jpg" media-type="image/jpeg"/>')
        src = _epub(tmp_path, opf, {"OEBPS/images/p1.jpg": first})
        out = _read(_embed_epub(_book(), src, _stored(first, tmp_path)))
        assert out["OEBPS/images/p1.jpg"] == first
        assert not any("tome-cover" in n for n in out)
        assert _cover_meta(out["OEBPS/content.opf"]) == []

    def test_no_named_cover_and_picked_adds_one_without_touching_images(self, tmp_path):
        first = _picture(1)
        picked = _picture(9, (800, 1200), "PNG")
        opf = _opf('<item id="i1" href="images/p1.jpg" media-type="image/jpeg"/>')
        src = _epub(tmp_path, opf, {"OEBPS/images/p1.jpg": first})
        out = _read(_embed_epub(_book(), src, picked))
        assert out["OEBPS/images/p1.jpg"] == first
        assert _format(out["OEBPS/tome-cover.jpg"]) == "JPEG"
        items = _items(out["OEBPS/content.opf"])
        assert items["tome-cover"] == {
            "id": "tome-cover", "href": "tome-cover.jpg",
            "media-type": "image/jpeg", "properties": "cover-image",
        }
        assert _cover_meta(out["OEBPS/content.opf"]) == ["tome-cover"]

    def test_added_cover_in_epub2_has_no_properties(self, tmp_path):
        src = _epub(tmp_path, _opf("", version="2.0"), {})
        out = _read(_embed_epub(_book(), src, _picture(9)))
        assert "properties" not in _items(out["OEBPS/content.opf"])["tome-cover"]
        assert "OEBPS/tome-cover.jpg" in out

    def test_no_tome_cover_leaves_the_file_cover(self, tmp_path):
        own = _picture(1)
        src = _epub(tmp_path, _opf(JPG_COVER), {"OEBPS/images/cover.jpg": own})
        out = _read(_embed_epub(_book(), src, None))
        assert out["OEBPS/images/cover.jpg"] == own


class TestCache:
    def test_copy_cached_by_an_older_embed_is_not_served(self, tmp_path, monkeypatch):
        from datetime import datetime
        from backend.services import metadata_embed as me

        monkeypatch.setattr(me.settings, "data_dir", tmp_path)
        pages = {"001.jpg": _picture(1)}
        src = _zip(tmp_path / "c.cbz", pages)
        book = _book()
        book.id = 7
        book.updated_at = datetime(2026, 1, 1)
        book.cover_path = None
        ts = int(book.updated_at.timestamp())
        old = tmp_path / "baked" / f"7_{ts}.cbz"
        old.parent.mkdir()
        old.write_bytes(b"cached by the old embed")

        served = me.get_baked_path(book, SimpleNamespace(file_path=str(src), format="cbz"))

        assert served != old and served.parent == old.parent
        assert not old.exists()
        assert "ComicInfo.xml" in _read(served.read_bytes())


# ── Through the download routes ──────────────────────────────────────────────

class TestDownloadRoutes:
    """The files from issue #344, uploaded and downloaded over HTTP."""

    def _dirs(self, tmp_path, monkeypatch):
        for name in ("library", "bindery", "data"):
            (tmp_path / name).mkdir()
        monkeypatch.setattr("backend.core.config.settings.library_dir", tmp_path / "library")
        monkeypatch.setattr("backend.core.config.settings.incoming_dir", tmp_path / "bindery")
        monkeypatch.setattr("backend.core.config.settings.data_dir", tmp_path / "data")

    def _upload(self, client, db, name: str, data: bytes):
        from backend.models.book import BookFile
        r = client.post("/api/books/upload", files={"file": (name, data, "application/octet-stream")})
        assert r.status_code == 201, r.text
        book_id = r.json()["id"]
        assert r.json()["cover_path"], "the upload should have extracted a cover"
        return book_id, db.query(BookFile).filter(BookFile.book_id == book_id).one().id

    def _comic(self, tmp_path) -> tuple[bytes, dict[str, bytes]]:
        pages = {f"00{i}.jpg": _picture(i) for i in (1, 2, 3)}
        return _zip(tmp_path / "Comic v01.cbz", pages).read_bytes(), pages

    def _book_file(self, tmp_path) -> tuple[bytes, bytes]:
        cover = _picture(4, (1600, 2400), "PNG")
        src = _epub(tmp_path, _opf(PNG_COVER), {"OEBPS/images/cover.png": cover})
        return src.read_bytes(), cover

    def test_comic_downloads_with_its_own_pages_only(self, client, db, tmp_path, monkeypatch):
        self._dirs(tmp_path, monkeypatch)
        data, pages = self._comic(tmp_path)
        book_id, file_id = self._upload(client, db, "Comic v01.cbz", data)

        web = client.get(f"/api/books/{book_id}/download/{file_id}")
        opds = client.get(f"/opds/download/{book_id}/{file_id}", auth=("testadmin", "adminpass123"))
        bulk = client.post("/api/downloads", json={"book_ids": [book_id]})
        assert (web.status_code, opds.status_code, bulk.status_code) == (200, 200, 200)

        inner = _read(bulk.content)
        assert len(inner) == 1
        for body in (web.content, opds.content, next(iter(inner.values()))):
            out = _read(body)
            assert sorted(out) == ["001.jpg", "002.jpg", "003.jpg", "ComicInfo.xml"]
            for name, page in pages.items():
                assert out[name] == page

    def test_epub_downloads_with_its_own_cover(self, client, db, tmp_path, monkeypatch):
        self._dirs(tmp_path, monkeypatch)
        data, cover = self._book_file(tmp_path)
        book_id, file_id = self._upload(client, db, "A Book.epub", data)

        web = client.get(f"/api/books/{book_id}/download/{file_id}")
        opds = client.get(f"/opds/download/{book_id}/{file_id}", auth=("testadmin", "adminpass123"))
        assert (web.status_code, opds.status_code) == (200, 200)
        for body in (web.content, opds.content):
            assert _read(body)["OEBPS/images/cover.png"] == cover

    def test_cover_picked_in_tome_reaches_both_downloads(self, client, db, tmp_path, monkeypatch):
        self._dirs(tmp_path, monkeypatch)
        comic, pages = self._comic(tmp_path)
        epub, _ = self._book_file(tmp_path)
        comic_id, comic_file = self._upload(client, db, "Comic v01.cbz", comic)
        epub_id, epub_file = self._upload(client, db, "A Book.epub", epub)
        picked = _picture(9, (800, 1200))
        for book_id in (comic_id, epub_id):
            r = client.post(f"/api/books/{book_id}/cover", files={"file": ("c.jpg", picked, "image/jpeg")})
            assert r.status_code == 200, r.text

        out = client.get(f"/api/books/{comic_id}/download/{comic_file}").content
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            assert zf.namelist()[0] == COVER_NAME
            assert zf.read(COVER_NAME) == picked
            assert zf.read("001.jpg") == pages["001.jpg"]

        out = _read(client.get(f"/api/books/{epub_id}/download/{epub_file}").content)
        new = out["OEBPS/images/cover.png"]
        assert _format(new) == "PNG"
        assert _cover_changed(picked, new) is False
