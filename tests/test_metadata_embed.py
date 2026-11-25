"""Tests for metadata embedding on download (backend/services/metadata_embed.py).

Regression: genre/category tags (book.tags) must be written back into
downloaded files — <dc:subject> for EPUB, <Genre> for CBZ ComicInfo — so the
import→edit→download round-trip preserves tags. Previously neither was written.
"""
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from backend.services.metadata_embed import _rewrite_opf, _build_comic_info, DC_NS


def _tag(name: str) -> SimpleNamespace:
    return SimpleNamespace(tag=name)


def _book(**overrides) -> SimpleNamespace:
    base = dict(
        title="A Book", subtitle=None, author="An Author", publisher=None,
        description=None, language=None, year=None, isbn=None,
        series=None, series_index=None, tags=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


MINIMAL_OPF = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf">'
    '<dc:title>Old</dc:title>'
    '<dc:subject>StaleTag</dc:subject>'
    '</metadata>'
    '<manifest><item id="t" href="t.xhtml" media-type="application/xhtml+xml"/></manifest>'
    '<spine><itemref idref="t"/></spine></package>'
).encode()


def _subjects(opf_bytes: bytes) -> list[str]:
    root = ET.fromstring(opf_bytes)
    return [el.text for el in root.iter(f"{{{DC_NS}}}subject")]


class TestEpubSubjectWrite:
    def test_tags_written_as_dc_subject(self):
        book = _book(tags=[_tag("Nonfiction"), _tag("Science")])
        out, _ = _rewrite_opf(MINIMAL_OPF, book, "content.opf", [], have_cover=False)
        assert _subjects(out) == ["Nonfiction", "Science"]

    def test_stale_subjects_replaced(self):
        # The pre-existing <dc:subject>StaleTag</dc:subject> must be cleared.
        book = _book(tags=[_tag("Fresh")])
        out, _ = _rewrite_opf(MINIMAL_OPF, book, "content.opf", [], have_cover=False)
        assert _subjects(out) == ["Fresh"]

    def test_no_tags_clears_subjects(self):
        book = _book(tags=[])
        out, _ = _rewrite_opf(MINIMAL_OPF, book, "content.opf", [], have_cover=False)
        assert _subjects(out) == []


class TestComicInfoGenreWrite:
    def test_tags_written_as_genre(self):
        book = _book(tags=[_tag("Action"), _tag("Adventure")])
        root = ET.fromstring(_build_comic_info(book))
        genre = root.find("Genre")
        assert genre is not None and genre.text == "Action, Adventure"

    def test_no_tags_no_genre(self):
        book = _book(tags=[])
        root = ET.fromstring(_build_comic_info(book))
        assert root.find("Genre") is None


CALIBRE_OPF = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf">'
    '<dc:title>Old</dc:title>'
    '<dc:identifier id="uuid_id" opf:scheme="uuid">324d8c1a</dc:identifier>'
    '<dc:identifier opf:scheme="ISBN">9780575048003</dc:identifier>'
    '</metadata>'
    '<manifest><item id="t" href="t.xhtml" media-type="application/xhtml+xml"/></manifest>'
    '<spine><itemref idref="t"/></spine></package>'
).encode()


def _identifiers(opf: bytes) -> list[tuple]:
    return [(el.get("id"), el.text) for el in ET.fromstring(opf).iter(f"{{{DC_NS}}}identifier")]


class TestEpubIdentifierWrite:
    def test_isbn_keeps_unique_identifier(self):
        out, _ = _rewrite_opf(CALIBRE_OPF, _book(isbn="9780060853983"), "content.opf", [], have_cover=False)
        assert _identifiers(out) == [("uuid_id", "324d8c1a"), (None, "9780060853983")]

    def test_isbn_unique_identifier_is_left_as_is(self):
        # font obfuscation keys are derived from the unique identifier's exact text
        opf = CALIBRE_OPF.replace(b'unique-identifier="uuid_id"', b'unique-identifier="BookId"').replace(
            b'<dc:identifier opf:scheme="ISBN">9780575048003', b'<dc:identifier id="BookId">urn:isbn:9780575048003'
        )
        out, _ = _rewrite_opf(opf, _book(isbn="9780060853983"), "content.opf", [], have_cover=False)
        assert _identifiers(out) == [
            ("uuid_id", "324d8c1a"),
            ("BookId", "urn:isbn:9780575048003"),
            (None, "9780060853983"),
        ]

    def test_isbn_already_in_unique_identifier_is_not_added_again(self):
        opf = CALIBRE_OPF.replace(b'unique-identifier="uuid_id"', b'unique-identifier="BookId"').replace(
            b'<dc:identifier opf:scheme="ISBN">9780575048003', b'<dc:identifier id="BookId">urn:isbn:978-0-06-085398-3'
        )
        out, _ = _rewrite_opf(opf, _book(isbn="9780060853983"), "content.opf", [], have_cover=False)
        assert _identifiers(out) == [("uuid_id", "324d8c1a"), ("BookId", "urn:isbn:978-0-06-085398-3")]

    def test_number_with_another_scheme_is_kept(self):
        opf = CALIBRE_OPF.replace(
            b"</metadata>", b'<dc:identifier opf:scheme="AMAZON">0575048006</dc:identifier></metadata>'
        )
        out, _ = _rewrite_opf(opf, _book(isbn="9780060853983"), "content.opf", [], have_cover=False)
        assert _identifiers(out) == [("uuid_id", "324d8c1a"), (None, "0575048006"), (None, "9780060853983")]

    def test_bare_isbn_is_replaced(self):
        opf = CALIBRE_OPF.replace(b'<dc:identifier opf:scheme="ISBN">', b"<dc:identifier>")
        out, _ = _rewrite_opf(opf, _book(isbn="9780060853983"), "content.opf", [], have_cover=False)
        assert _identifiers(out) == [("uuid_id", "324d8c1a"), (None, "9780060853983")]
