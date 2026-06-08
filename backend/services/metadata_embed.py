"""Embed Tome's canonical metadata into a book file at download time.

The output is cached at ``{data_dir}/baked/{book_id}_{updated_at_ts}.{ext}``.
Cache is invalidated automatically when ``Book.updated_at`` advances, because
the timestamp is part of the filename. Stale entries for a given book are
purged on write.

EPUB and CBZ get full metadata + cover embed. PDF gets metadata dict only
(cover embedding in PDF is fragile and the library has almost none).
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from backend.core.config import settings
from backend.models.book import Book, BookFile
from backend.services.metadata import sha256_file
from backend.services.xml_ns import namespaces

log = logging.getLogger(__name__)

# Formats whose bytes we can rewrite with embedded metadata. Everything else is
# served/left as-is.
BAKEABLE_FORMATS = {"epub", "cbz", "pdf"}

# Image entries are already compressed; re-deflating them is pure CPU cost for
# zero size win (and, for big CBZ archives, the bulk of the bake time). Store
# them uncompressed instead. Text-ish entries (XHTML, CSS, XML) still deflate.
_STORED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".jxl"}


def _compress_for(name: str) -> int:
    ext = Path(name).suffix.lower()
    return zipfile.ZIP_STORED if ext in _STORED_EXTS else zipfile.ZIP_DEFLATED

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

# Re-asserted at serialization time rather than relying on global,
# import-order-dependent state — see ``backend/services/xml_ns.py`` (GH #15).
_NS_REGISTRATIONS = (
    ("", OPF_NS),
    ("dc", DC_NS),
    ("opf", OPF_NS),
)


# ── Cache plumbing ────────────────────────────────────────────────────────────

def _baked_dir() -> Path:
    d = settings.data_dir / "baked"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(book: Book) -> str:
    ts = int(book.updated_at.timestamp())
    return f"{book.id}_{ts}"


def _purge_stale(book_id: int, keep: Optional[str] = None) -> None:
    for p in _baked_dir().glob(f"{book_id}_*"):
        if keep and p.stem == keep:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def purge_book_cache(book_id: int) -> None:
    """Call on book update or delete to discard every cached bake."""
    _purge_stale(book_id)


def get_baked_path(book: Book, book_file: BookFile) -> Path:
    """Return a path to a baked copy of ``book_file`` with Tome metadata
    embedded. Bakes + caches on first call; returns cached path thereafter.

    Falls back to the raw file path if baking fails for any reason.
    """
    src = Path(book_file.file_path)
    if not src.exists():
        return src

    # If the on-disk file was already baked in place for this exact metadata
    # revision, its bytes are current — serve the raw file, skip embed + cache.
    if (
        book_file.metadata_synced_at is not None
        and book.updated_at is not None
        and book_file.metadata_synced_at == book.updated_at
    ):
        return src

    key = _cache_key(book)
    baked = _baked_dir() / f"{key}.{book_file.format}"
    if baked.exists() and baked.stat().st_size > 0:
        return baked

    _purge_stale(book.id, keep=key)

    try:
        cover_bytes = _load_cover(book)
        out = _embed(book, src, book_file.format, cover_bytes)
    except Exception as e:
        log.warning("metadata embed failed for book_id=%s (%s); serving raw", book.id, e)
        return src

    if out is None:
        return src

    tmp = baked.with_suffix(baked.suffix + ".tmp")
    try:
        tmp.write_bytes(out)
        tmp.replace(baked)
    except OSError as e:
        log.warning("failed to write baked cache for book_id=%s: %s", book.id, e)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return src
    return baked


def _load_cover(book: Book) -> Optional[bytes]:
    if not book.cover_path:
        return None
    p = settings.covers_dir / book.cover_path
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _embed(book: Book, src: Path, fmt: str, cover_bytes: Optional[bytes]) -> Optional[bytes]:
    fmt = (fmt or "").lower()
    if fmt == "epub":
        return _embed_epub(book, src, cover_bytes)
    if fmt == "cbz":
        return _embed_cbz(book, src, cover_bytes)
    if fmt == "pdf":
        return _embed_pdf(book, src)
    return None  # unsupported format — fall back to raw file


# ── EPUB ──────────────────────────────────────────────────────────────────────

def _embed_epub(book: Book, src: Path, cover_bytes: Optional[bytes]) -> Optional[bytes]:
    with zipfile.ZipFile(src, "r") as zf:
        names = zf.namelist()
        try:
            container = zf.read("META-INF/container.xml")
        except KeyError:
            return None
        opf_path = _find_opf_path(container)
        if opf_path is None or opf_path not in names:
            return None
        opf_xml = zf.read(opf_path)

        new_opf, cover_href = _rewrite_opf(opf_xml, book, opf_path, names, cover_bytes is not None)
        if new_opf is None:
            return None

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            # Preserve mimetype first, uncompressed, per EPUB spec
            if "mimetype" in names:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                zout.writestr(info, zf.read("mimetype"))
            for n in names:
                if n == "mimetype":
                    continue
                if n == opf_path:
                    zout.writestr(n, new_opf)
                    continue
                if cover_bytes is not None and cover_href and _same_href(n, opf_path, cover_href):
                    zout.writestr(n, cover_bytes)
                    continue
                zout.writestr(n, zf.read(n))
        return buf.getvalue()


def _find_opf_path(container_xml: bytes) -> Optional[str]:
    try:
        root = ET.fromstring(container_xml)
    except ET.ParseError:
        return None
    rootfile = root.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        return None
    return rootfile.get("full-path")


def _rewrite_opf(
    opf_xml: bytes, book: Book, opf_path: str, zip_names: list[str], have_cover: bool
) -> tuple[Optional[bytes], Optional[str]]:
    try:
        tree = ET.ElementTree(ET.fromstring(opf_xml))
    except ET.ParseError:
        return None, None
    root = tree.getroot()
    meta = root.find(f"{{{OPF_NS}}}metadata")
    if meta is None:
        return None, None

    def _clear(tag: str) -> None:
        for el in list(meta.findall(f"{{{DC_NS}}}{tag}")):
            meta.remove(el)

    def _set(tag: str, value: Optional[str]) -> None:
        _clear(tag)
        if value is None or value == "":
            return
        el = ET.SubElement(meta, f"{{{DC_NS}}}{tag}")
        el.text = str(value)

    full_title = book.title or ""
    if book.subtitle:
        full_title = f"{full_title}: {book.subtitle}" if full_title else book.subtitle
    _set("title", full_title or None)
    _set("creator", book.author)
    _set("publisher", book.publisher)
    _set("description", book.description)
    _set("language", book.language)
    if book.year:
        _set("date", f"{book.year:04d}-01-01")
    if book.isbn:
        _clear("identifier")
        el = ET.SubElement(meta, f"{{{DC_NS}}}identifier")
        el.set(f"{{{OPF_NS}}}scheme", "ISBN")
        el.text = book.isbn

    # Calibre series conventions (KOReader + Calibre honour these)
    for el in list(meta.findall(f"{{{OPF_NS}}}meta")):
        name = el.get("name", "")
        if name in ("calibre:series", "calibre:series_index"):
            meta.remove(el)
    if book.series:
        ET.SubElement(meta, f"{{{OPF_NS}}}meta", {"name": "calibre:series", "content": book.series})
    if book.series_index is not None:
        idx = book.series_index
        idx_s = str(int(idx)) if idx == int(idx) else str(idx)
        ET.SubElement(meta, f"{{{OPF_NS}}}meta", {"name": "calibre:series_index", "content": idx_s})

    # Genre/category tags — one <dc:subject> per tag (Calibre convention).
    _clear("subject")
    for t in book.tags:
        tag_val = (t.tag or "").strip()
        if tag_val:
            el = ET.SubElement(meta, f"{{{DC_NS}}}subject")
            el.text = tag_val

    # Cover reference — return the in-zip path of the current cover image so
    # the caller can overwrite its bytes. Resolved relative to the OPF.
    cover_href: Optional[str] = None
    if have_cover:
        cover_href = _find_cover_href(root)

    out = io.BytesIO()
    with namespaces(_NS_REGISTRATIONS):
        tree.write(out, encoding="utf-8", xml_declaration=True)
    return out.getvalue(), cover_href


def _find_cover_href(root: ET.Element) -> Optional[str]:
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return None
    # EPUB 3: properties="cover-image"
    for item in manifest.findall(f"{{{OPF_NS}}}item"):
        props = item.get("properties", "")
        if "cover-image" in props.split():
            return item.get("href")
    # EPUB 2: <meta name="cover" content="<item-id>"/>
    meta = root.find(f"{{{OPF_NS}}}metadata")
    cover_id = None
    if meta is not None:
        for el in meta.findall(f"{{{OPF_NS}}}meta"):
            if el.get("name") == "cover":
                cover_id = el.get("content")
                break
    if cover_id:
        for item in manifest.findall(f"{{{OPF_NS}}}item"):
            if item.get("id") == cover_id:
                return item.get("href")
    # Fallback: first image item in manifest
    for item in manifest.findall(f"{{{OPF_NS}}}item"):
        mt = item.get("media-type", "")
        if mt.startswith("image/"):
            return item.get("href")
    return None


def _same_href(zip_name: str, opf_path: str, href: str) -> bool:
    opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    resolved = f"{opf_dir}/{href}" if opf_dir else href
    # Normalise "./a" vs "a" and "a/../b" segments
    parts: list[str] = []
    for seg in resolved.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts) == zip_name


# ── CBZ ───────────────────────────────────────────────────────────────────────

COVER_NAME = "000_cover.jpg"
COMIC_INFO_NAME = "ComicInfo.xml"


def _embed_cbz(book: Book, src: Path, cover_bytes: Optional[bytes]) -> Optional[bytes]:
    comic_info = _build_comic_info(book)

    with zipfile.ZipFile(src, "r") as zf:
        names = zf.namelist()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            if cover_bytes is not None:
                zout.writestr(COVER_NAME, cover_bytes, compress_type=zipfile.ZIP_STORED)
            for n in names:
                # Skip the old ComicInfo — we rewrite it below
                if n.lower() == "comicinfo.xml":
                    continue
                # Skip the slot we're writing the Tome cover into (if any)
                if cover_bytes is not None and n == COVER_NAME:
                    continue
                # Pages are already-compressed images → store, don't re-deflate.
                zout.writestr(n, zf.read(n), compress_type=_compress_for(n))
            zout.writestr(COMIC_INFO_NAME, comic_info)
        return buf.getvalue()


def _build_comic_info(book: Book) -> bytes:
    root = ET.Element("ComicInfo", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    })

    def _add(tag: str, value) -> None:
        if value is None or value == "":
            return
        el = ET.SubElement(root, tag)
        el.text = str(value)

    _add("Title", book.title)
    _add("Series", book.series)
    if book.series_index is not None:
        idx = book.series_index
        _add("Number", str(int(idx)) if idx == int(idx) else str(idx))
    _add("Summary", book.description)
    _add("Writer", book.author)
    _add("Publisher", book.publisher)
    if book.year:
        _add("Year", book.year)
    if book.language:
        _add("LanguageISO", book.language)
    tags = [t.tag.strip() for t in book.tags if t.tag and t.tag.strip()]
    if tags:
        _add("Genre", ", ".join(tags))

    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


# ── PDF ───────────────────────────────────────────────────────────────────────

def _embed_pdf(book: Book, src: Path) -> Optional[bytes]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        doc = fitz.open(str(src))
    except Exception:
        return None
    try:
        meta = dict(doc.metadata or {})
        meta["title"] = book.title or meta.get("title", "")
        if book.author:
            meta["author"] = book.author
        if book.description:
            meta["subject"] = book.description
        doc.set_metadata(meta)
        buf = doc.tobytes()
    finally:
        doc.close()
    return buf


# ── In-file bake (write metadata into the source file on disk) ─────────────────
#
# Unlike get_baked_path (which writes a disposable cache copy), bake_to_file
# mutates the real library file. It is destructive + irreversible, so the heavy
# lifting here is safety: validate the freshly-embedded bytes parse, write to a
# sibling tmp, fsync, then atomically os.replace the original. A buggy embed must
# never clobber a good source.

@dataclass
class BakeFileResult:
    """Outcome of baking one BookFile in place."""
    file_path: str
    status: str  # "baked" | "skipped" | "readonly" | "failed"
    reason: Optional[str] = None
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "baked"


def library_writable() -> bool:
    """Is the library root writable? Used to gate the bake UI on a `:ro` mount."""
    try:
        return os.access(settings.library_dir, os.W_OK)
    except OSError:
        return False


def _validate_baked(out: bytes, fmt: str) -> bool:
    """Cheap sanity check that the embedded bytes are a usable file of `fmt`
    before we let them replace the original."""
    fmt = (fmt or "").lower()
    if fmt in ("epub", "cbz"):
        try:
            with zipfile.ZipFile(io.BytesIO(out), "r") as zf:
                if zf.testzip() is not None:
                    return False
                if fmt == "epub" and "META-INF/container.xml" not in zf.namelist():
                    return False
            return True
        except (zipfile.BadZipFile, OSError):
            return False
    if fmt == "pdf":
        return out[:5] == b"%PDF-"
    return False


def bake_to_file(book: Book, book_file: BookFile) -> BakeFileResult:
    """Write Tome's metadata into ``book_file`` on disk, in place.

    Mutates ``book_file.content_hash`` + ``book_file.metadata_synced_at`` on the
    attached ORM objects **without committing** — the caller owns the transaction.
    Per-file isolated: any failure leaves the original file untouched, never
    leaks a ``.tmp``, and returns a non-``baked`` status rather than raising.
    """
    fmt = (book_file.format or "").lower()
    src = Path(book_file.file_path)

    if fmt not in BAKEABLE_FORMATS:
        return BakeFileResult(str(src), "skipped", reason="unsupported format")
    if not src.exists():
        return BakeFileResult(str(src), "skipped", reason="file missing")
    if not os.access(src.parent, os.W_OK):
        return BakeFileResult(str(src), "readonly", reason="directory not writable")

    try:
        cover_bytes = _load_cover(book)
        out = _embed(book, src, fmt, cover_bytes)
    except Exception as e:  # noqa: BLE001 — embed must never abort a bulk run
        log.warning("bake: embed failed for book_id=%s file=%s: %s", book.id, src.name, e)
        return BakeFileResult(str(src), "failed", reason=f"embed error: {e}")

    if out is None:
        # _embed returns None for formats it can't handle (e.g. PDF without
        # PyMuPDF, or a malformed EPUB with no OPF).
        return BakeFileResult(str(src), "skipped", reason="nothing to embed")

    if not _validate_baked(out, fmt):
        return BakeFileResult(str(src), "failed", reason="validation failed — original kept")

    old_hash = book_file.content_hash
    tmp = src.with_suffix(src.suffix + ".bake.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(out)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, src)  # atomic within the same directory
    except OSError as e:
        log.warning("bake: write failed for book_id=%s file=%s: %s", book.id, src.name, e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return BakeFileResult(str(src), "failed", reason=f"write error: {e}")

    new_hash = sha256_file(src)
    book_file.content_hash = new_hash
    book_file.metadata_synced_at = book.updated_at
    try:
        book_file.file_size = src.stat().st_size
    except OSError:
        pass

    # The lazy cache for this book is now redundant.
    try:
        _purge_stale(book.id)
    except OSError:
        pass

    return BakeFileResult(str(src), "baked", old_hash=old_hash, new_hash=new_hash)
