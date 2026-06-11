"""Tests for in-file metadata baking (backend/services/metadata_embed.bake_to_file).

The embed itself is covered by test_metadata_embed.py; this focuses on the
safety machinery: validate-before-replace, atomic write, hash recompute,
metadata_synced_at semantics, the read-only guard, and per-file failure
isolation (a bad embed must never clobber a good source).
"""
import zipfile
from datetime import timedelta

import pytest

from backend.services import metadata_embed as me
from backend.services.metadata_embed import (
    bake_to_file, get_baked_path, _compress_for, _validate_baked,
)
from backend.services.metadata import sha256_file


# ── builders ───────────────────────────────────────────────────────────────────

def _make_epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "content.opf",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf"><dc:title>Original Title</dc:title>'
            '</metadata>'
            '<manifest><item id="t" href="t.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="t"/></spine></package>',
        )
        z.writestr("t.xhtml", "<html><body>hi</body></html>")


def _make_cbz(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("001.jpg", b"\xff\xd8\xff\xe0" + b"fakejpeg" * 50)
        z.writestr("002.jpg", b"\xff\xd8\xff\xe0" + b"moredata" * 50)


# ── unit: helpers ────────────────────────────────────────────────────────────

def test_compress_for_images_stored():
    assert _compress_for("001.jpg") == zipfile.ZIP_STORED
    assert _compress_for("cover.PNG") == zipfile.ZIP_STORED
    assert _compress_for("page.xhtml") == zipfile.ZIP_DEFLATED
    assert _compress_for("ComicInfo.xml") == zipfile.ZIP_DEFLATED


def test_validate_baked():
    assert _validate_baked(b"%PDF-1.7 ...", "pdf") is True
    assert _validate_baked(b"not a pdf", "pdf") is False
    assert _validate_baked(b"not a zip", "epub") is False
    assert _validate_baked(b"not a zip", "cbz") is False


# ── bake: EPUB happy path ──────────────────────────────────────────────────────

def test_bake_epub_rewrites_metadata_and_hash(db, make_book, tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub)
    book = make_book(title="Baked Title", author="Jane Doe", series="My Series",
                     series_index=3, file_path=str(epub), file_format="epub",
                     content_hash="oldhash")
    bf = book.files[0]

    result = bake_to_file(book, bf)
    db.commit()

    assert result.status == "baked"
    # File is still a valid EPUB carrying Tome's title.
    with zipfile.ZipFile(epub, "r") as z:
        assert z.testzip() is None
        opf = z.read("content.opf").decode()
    assert "Baked Title" in opf
    assert "calibre:series" in opf and "My Series" in opf
    # Hash recomputed to match the new bytes, and the file is marked synced.
    assert bf.content_hash == sha256_file(epub)
    assert bf.content_hash != "oldhash"
    assert bf.metadata_synced_at == book.updated_at


# ── bake: CBZ stores pages uncompressed ────────────────────────────────────────

def test_bake_cbz_embeds_comicinfo_and_stores_images(db, make_book, tmp_path):
    cbz = tmp_path / "comic.cbz"
    _make_cbz(cbz)
    book = make_book(title="Vol 1", series="Comic Series", series_index=1,
                     file_path=str(cbz), file_format="cbz")
    bf = book.files[0]

    result = bake_to_file(book, bf)
    db.commit()

    assert result.status == "baked"
    with zipfile.ZipFile(cbz, "r") as z:
        assert z.testzip() is None
        names = z.namelist()
        assert "ComicInfo.xml" in names
        assert "Comic Series" in z.read("ComicInfo.xml").decode()
        # Image pages must be stored, not re-deflated.
        assert z.getinfo("001.jpg").compress_type == zipfile.ZIP_STORED


# ── get_baked_path early-return ────────────────────────────────────────────────

def test_get_baked_path_serves_raw_when_synced(db, make_book, tmp_path):
    epub = tmp_path / "synced.epub"
    _make_epub(epub)
    book = make_book(title="Synced", file_path=str(epub), file_format="epub")
    bf = book.files[0]

    bake_to_file(book, bf)
    db.commit()

    # Already current → raw path, no cache copy.
    assert get_baked_path(book, bf) == epub

    # Simulate a later metadata edit bumping updated_at → stale → lazy-bake again.
    book.updated_at = book.updated_at + timedelta(seconds=5)
    db.flush()
    out = get_baked_path(book, bf)
    assert out != epub
    assert out.exists()


# ── safety: read-only directory ────────────────────────────────────────────────

def test_bake_readonly_dir_leaves_file_untouched(db, make_book, tmp_path, monkeypatch):
    epub = tmp_path / "ro.epub"
    _make_epub(epub)
    before = epub.read_bytes()
    book = make_book(title="RO", file_path=str(epub), file_format="epub")
    bf = book.files[0]
    bf.content_hash = "keepme"
    db.flush()

    monkeypatch.setattr(me.os, "access", lambda p, mode: False)
    result = bake_to_file(book, bf)

    assert result.status == "readonly"
    assert epub.read_bytes() == before
    assert bf.content_hash == "keepme"
    assert bf.metadata_synced_at is None


# ── safety: corrupt embed must not replace the original ─────────────────────────

def test_bake_corrupt_embed_fails_safe(db, make_book, tmp_path, monkeypatch):
    epub = tmp_path / "corrupt.epub"
    _make_epub(epub)
    before = epub.read_bytes()
    book = make_book(title="Corrupt", file_path=str(epub), file_format="epub")
    bf = book.files[0]

    monkeypatch.setattr(me, "_embed", lambda *a, **k: b"not a real zip")
    result = bake_to_file(book, bf)

    assert result.status == "failed"
    assert "validation" in (result.reason or "")
    assert epub.read_bytes() == before          # original intact
    assert not (tmp_path / "corrupt.epub.bake.tmp").exists()  # no leftover tmp
    assert bf.metadata_synced_at is None


def test_bake_embed_raises_fails_safe(db, make_book, tmp_path, monkeypatch):
    epub = tmp_path / "boom.epub"
    _make_epub(epub)
    before = epub.read_bytes()
    book = make_book(title="Boom", file_path=str(epub), file_format="epub")
    bf = book.files[0]

    def _boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(me, "_embed", _boom)
    result = bake_to_file(book, bf)

    assert result.status == "failed"
    assert epub.read_bytes() == before


# ── skips ───────────────────────────────────────────────────────────────────

def test_bake_unsupported_format_skipped(db, make_book, tmp_path):
    mobi = tmp_path / "x.mobi"
    mobi.write_bytes(b"whatever")
    book = make_book(title="Mobi", file_path=str(mobi), file_format="mobi")
    result = bake_to_file(book, book.files[0])
    assert result.status == "skipped"
    assert "format" in (result.reason or "")


def test_bake_missing_file_skipped(db, make_book, tmp_path):
    book = make_book(title="Gone", file_path=str(tmp_path / "nope.epub"),
                     file_format="epub")
    result = bake_to_file(book, book.files[0])
    assert result.status == "skipped"
    assert "missing" in (result.reason or "")
