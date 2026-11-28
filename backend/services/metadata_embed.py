"""Embed Tome's canonical metadata into a book file at download time.

The output is cached at
``{data_dir}/baked/{book_id}_{updated_at_ts}_e{version}.{ext}``. Cache is
invalidated automatically when ``Book.updated_at`` advances, because the
timestamp is part of the filename, and when ``_EMBED_VERSION`` is bumped.
Stale entries for a given book are purged on write.

EPUB and CBZ get full metadata. Their cover is touched only when the cover
Tome shows is a different picture from the one the file already carries
(``_cover_changed``): a file whose cover in Tome is its own, or another scan
of the same artwork, keeps its own image and its own pages. PDF gets metadata
dict only (cover embedding in PDF is fragile and the library has almost none).
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from PIL import Image

from backend.core.config import settings
from backend.models.book import Book, BookFile
from backend.services.xml_ns import namespaces

log = logging.getLogger(__name__)

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


# Bump when the embed writes different bytes for the same book, so copies
# cached by an older version are not served again. 2: covers (#344).
_EMBED_VERSION = 2


def _cache_key(book: Book) -> str:
    ts = int(book.updated_at.timestamp())
    return f"{book.id}_{ts}_e{_EMBED_VERSION}"


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

        plan = _plan_epub_cover(zf, opf_xml, opf_path, names, cover_bytes)
        new_opf, _ = _rewrite_opf(
            opf_xml, book, opf_path, names, False,
            add_cover=(plan.href, plan.media_type) if plan and plan.add else None,
        )
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
                if plan and not plan.add and n == plan.zip_name:
                    zout.writestr(n, plan.data)
                    continue
                zout.writestr(n, zf.read(n))
            if plan and plan.add:
                zout.writestr(plan.zip_name, plan.data)
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


def _is_isbn_identifier(el: ET.Element) -> bool:
    text = (el.text or "").strip().lower()
    scheme = el.get(f"{{{OPF_NS}}}scheme")
    if (scheme or "").upper() == "ISBN" or text.startswith(("urn:isbn:", "isbn:")):
        return True
    # A bare ISBN, as extract_epub reads it, only without a scheme (calibre's AMAZON id is one too)
    return scheme is None and re.fullmatch(r"\d{9,12}[\dx]", text.replace("-", "")) is not None


def _isbn_digits(text: str) -> str:
    text = text.strip().lower().removeprefix("urn:isbn:").removeprefix("isbn:")
    return text.replace("-", "").replace(" ", "")


def _rewrite_opf(
    opf_xml: bytes, book: Book, opf_path: str, zip_names: list[str], have_cover: bool,
    add_cover: Optional[tuple[str, str]] = None,
) -> tuple[Optional[bytes], Optional[str]]:
    """``add_cover`` = (href, media type) of a cover image the caller adds to
    the archive: it is declared in the manifest and named as the cover."""
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
        # Replace only ISBN identifiers. The one package@unique-identifier names stays as it
        # is: font obfuscation keys are derived from it.
        unique_id = root.get("unique-identifier")
        has_isbn = False
        for el in list(meta.findall(f"{{{DC_NS}}}identifier")):
            if not _is_isbn_identifier(el):
                continue
            if unique_id is not None and el.get("id") == unique_id:
                has_isbn = _isbn_digits(el.text or "") == _isbn_digits(book.isbn)
                continue
            meta.remove(el)
            # EPUB 3: metas that refine the removed identifier would point at nothing
            if el.get("id"):
                for ref in list(meta.findall(f"{{{OPF_NS}}}meta")):
                    if ref.get("refines") == f"#{el.get('id')}":
                        meta.remove(ref)
        if not has_isbn:
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

    if add_cover is not None:
        _declare_cover(root, meta, *add_cover)

    out = io.BytesIO()
    with namespaces(_NS_REGISTRATIONS):
        tree.write(out, encoding="utf-8", xml_declaration=True)
    return out.getvalue(), cover_href


def _declare_cover(root: ET.Element, meta: ET.Element, href: str, media_type: str) -> None:
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return
    ids = {item.get("id") for item in manifest.findall(f"{{{OPF_NS}}}item")}
    item_id = "tome-cover"
    n = 1
    while item_id in ids:
        n += 1
        item_id = f"tome-cover-{n}"
    attrs = {"id": item_id, "href": href, "media-type": media_type}
    if (root.get("version") or "").startswith("3"):
        attrs["properties"] = "cover-image"
    ET.SubElement(manifest, f"{{{OPF_NS}}}item", attrs)
    for el in list(meta.findall(f"{{{OPF_NS}}}meta")):
        if el.get("name") == "cover":
            meta.remove(el)
    ET.SubElement(meta, f"{{{OPF_NS}}}meta", {"name": "cover", "content": item_id})


def _find_cover_href(root: ET.Element) -> Optional[str]:
    item = _declared_cover_item(root)
    if item is None:
        item = _fallback_cover_item(root)
    return item.get("href") if item is not None else None


def _declared_cover_item(root: ET.Element) -> Optional[ET.Element]:
    """The manifest item the package itself names as its cover image."""
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return None
    # EPUB 3: properties="cover-image"
    for item in manifest.findall(f"{{{OPF_NS}}}item"):
        props = item.get("properties", "")
        if "cover-image" in props.split():
            return item
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
                return item
    return None


def _fallback_cover_item(root: ET.Element) -> Optional[ET.Element]:
    """The image Tome's own extraction (``metadata.extract_epub``) falls back
    to when the package names no cover: one whose name says cover, else the
    first image."""
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return None
    images = [
        item for item in manifest.findall(f"{{{OPF_NS}}}item")
        if item.get("media-type", "").startswith("image/")
    ]
    for item in images:
        name = (item.get("href") or "").lower()
        if any(kw in name for kw in ("cover", "front", "thumb")):
            return item
    return images[0] if images else None


def _resolve_href(opf_path: str, href: str) -> str:
    """The in-zip name an OPF-relative href points at."""
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
    return "/".join(parts)


def _same_href(zip_name: str, opf_path: str, href: str) -> bool:
    return _resolve_href(opf_path, href) == zip_name


# ── Cover ─────────────────────────────────────────────────────────────────────

# Image types a cover can be written as, by manifest media type.
_PIL_FORMATS = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}

_THUMB = 16
# Mean per-channel difference (0-255) between two 16x16 thumbnails above which
# two covers count as different pictures. Measured on a real library: Tome's
# stored copy of the file's own cover stays under 3, a fetched scan of the
# same artwork under 9, another edition's cover starts at 16 and an unrelated
# cover is above 30.
_SAME_COVER_MAX_DIFF = 12.0
_SAME_COVER_MAX_ASPECT_DIFF = 0.05
# A cover of the same picture replaces the file's own only when it is at
# least this much wider.
_UPGRADE_MIN_WIDTH_RATIO = 1.2


def _thumbprint(data: bytes) -> Optional[tuple[tuple[int, int], bytes]]:
    try:
        img = Image.open(io.BytesIO(data))
        size = img.size
        if not size[0] or not size[1]:
            return None
        small = img.convert("RGB").resize((_THUMB, _THUMB), Image.BOX)
        return size, small.tobytes()
    except Exception:
        return None


def _cover_changed(own: Optional[bytes], tome: bytes, allow_upgrade: bool = False) -> bool:
    """True when the cover Tome shows should go into the file.

    Tome's stored cover is usually the file's own cover scaled down and saved
    as JPEG (``metadata.save_cover``), or a fetched scan of the same artwork.
    Writing either back only makes the file worse, so the embed asks this
    first. The answer is yes when Tome shows a different picture, and, with
    ``allow_upgrade``, when it shows the same picture clearly larger. A Tome
    cover that cannot be read is never written; a file with no readable cover
    of its own always takes Tome's.
    """
    mine = _thumbprint(tome)
    if mine is None:
        return False
    if own is None:
        return True
    theirs = _thumbprint(own)
    if theirs is None:
        return True
    (mw, mh), (tw, th) = mine[0], theirs[0]
    if abs(mw / mh - tw / th) / (tw / th) > _SAME_COVER_MAX_ASPECT_DIFF:
        return True
    diff = sum(abs(a - b) for a, b in zip(mine[1], theirs[1])) / len(mine[1])
    if diff > _SAME_COVER_MAX_DIFF:
        return True
    return allow_upgrade and mw >= tw * _UPGRADE_MIN_WIDTH_RATIO


def _cover_as(data: bytes, fmt: str) -> Optional[bytes]:
    """``data`` as an image of PIL format ``fmt``, untouched when it already
    is one. None when it cannot be read or written."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format == fmt:
            return data
        if fmt == "JPEG" or img.mode not in ("RGB", "RGBA", "L", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        if fmt == "JPEG":
            img.save(out, "JPEG", quality=90, optimize=True)
        else:
            img.save(out, fmt)
        return out.getvalue()
    except Exception as e:
        log.warning("cover could not be written as %s: %s", fmt, e)
        return None


class _CoverPlan:
    """What the EPUB embed does with the cover: overwrite the image the
    package names (``add`` False) or add a new one and declare it."""

    def __init__(self, zip_name: str, data: bytes, add: bool = False,
                 href: str = "", media_type: str = "") -> None:
        self.zip_name = zip_name
        self.data = data
        self.add = add
        self.href = href
        self.media_type = media_type


def _plan_epub_cover(
    zf: zipfile.ZipFile, opf_xml: bytes, opf_path: str, names: list[str],
    cover_bytes: Optional[bytes],
) -> Optional[_CoverPlan]:
    """None = leave the file's cover alone."""
    if cover_bytes is None:
        return None
    try:
        root = ET.fromstring(opf_xml)
    except ET.ParseError:
        return None

    declared = _declared_cover_item(root)
    if declared is not None:
        zip_name = _resolve_href(opf_path, declared.get("href") or "")
        if zip_name not in names:
            return None
        if not _cover_changed(zf.read(zip_name), cover_bytes, allow_upgrade=True):
            return None
        # Keep the image's name and type: cover pages inside the book point at
        # this file, so the new cover is written in the format it already has.
        fmt = _PIL_FORMATS.get((declared.get("media-type") or "").lower())
        if fmt is None:
            return None
        data = _cover_as(cover_bytes, fmt)
        return _CoverPlan(zip_name, data) if data is not None else None

    # No cover named by the package. Tome's own cover then came from a
    # fallback image, which may be an illustration: never overwrite it.
    fallback = _fallback_cover_item(root)
    own: Optional[bytes] = None
    if fallback is not None:
        zip_name = _resolve_href(opf_path, fallback.get("href") or "")
        if zip_name in names:
            own = zf.read(zip_name)
    if not _cover_changed(own, cover_bytes):
        return None
    data = _cover_as(cover_bytes, "JPEG")
    if data is None:
        return None
    href = "tome-cover.jpg"
    n = 1
    while _resolve_href(opf_path, href) in names:
        n += 1
        href = f"tome-cover-{n}.jpg"
    return _CoverPlan(_resolve_href(opf_path, href), data, add=True,
                      href=href, media_type="image/jpeg")


# ── CBZ ───────────────────────────────────────────────────────────────────────

COVER_NAME = "000_cover.jpg"
COMIC_INFO_NAME = "ComicInfo.xml"


def _embed_cbz(book: Book, src: Path, cover_bytes: Optional[bytes]) -> Optional[bytes]:
    comic_info = _build_comic_info(book)

    with zipfile.ZipFile(src, "r") as zf:
        names = zf.namelist()
        cover_bytes = _cbz_cover_page(zf, names, cover_bytes)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            if cover_bytes is not None:
                zout.writestr(COVER_NAME, cover_bytes)
            for n in names:
                # Skip the old ComicInfo — we rewrite it below
                if n.lower() == "comicinfo.xml":
                    continue
                # Skip the slot we're writing the Tome cover into (if any)
                if cover_bytes is not None and n == COVER_NAME:
                    continue
                zout.writestr(n, zf.read(n))
            zout.writestr(COMIC_INFO_NAME, comic_info)
        return buf.getvalue()


def _cbz_cover_page(
    zf: zipfile.ZipFile, names: list[str], cover_bytes: Optional[bytes]
) -> Optional[bytes]:
    """The cover page to put in front of the comic, or None for no page.

    A reader shows the first page as the cover, so a page is added only when
    Tome's cover is a different picture from that page (someone picked
    another cover). Otherwise it would be the first page twice.
    """
    if cover_bytes is None:
        return None
    # Same choice of first page as metadata.extract_cbz
    images = sorted(
        n for n in names
        if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        and not n.startswith("__MACOSX")
    )
    own = zf.read(images[0]) if images else None
    if not _cover_changed(own, cover_bytes):
        return None
    return _cover_as(cover_bytes, "JPEG")


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
