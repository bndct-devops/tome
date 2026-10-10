"""A short plain-text sample from the first pages of a book file.

Used as identification evidence: the filename is where the mess comes from,
the title page and the first chapter rarely lie.

- EPUB: spine order from the OPF, skipping cover documents, until two
  documents with real text have been read (title pages and copyright pages
  are short, so a few more may be read to get there).
- PDF: the first three pages via PyMuPDF.
- Comics (CBZ/CBR) and anything else: no text (``None``).

Read straight from the zip rather than through ebooklib, which refuses
technically malformed but perfectly readable EPUBs.
"""
from __future__ import annotations

import html
import logging
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from backend.services.metadata import _html_to_text

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 3000
PDF_PAGES = 3
EPUB_DOCS_WANTED = 2      # documents with real text
EPUB_DOC_MIN_CHARS = 200  # what counts as "real text"
EPUB_MAX_DOCS_SCANNED = 8

_HTML_TYPES = {"application/xhtml+xml", "text/html", "application/xml"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _epub_spine(zf: zipfile.ZipFile) -> list[tuple[str, bool]]:
    """Spine documents in reading order as (zip member name, looks_like_cover).

    Falls back to every (X)HTML member in name order when the package cannot
    be read."""
    names = set(zf.namelist())
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = next(el for el in container.iter() if _local(el.tag) == "rootfile")
        opf_path = rootfile.attrib["full-path"]
        opf = ET.fromstring(zf.read(opf_path))
    except Exception:  # noqa: BLE001 - malformed package: use the fallback
        return [(n, "cover" in n.lower()) for n in sorted(names)
                if n.lower().endswith((".xhtml", ".html", ".htm"))]

    base = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str, str]] = {}
    cover_ids: set[str] = set()
    for el in opf.iter():
        tag = _local(el.tag)
        if tag == "item":
            item_id = el.attrib.get("id", "")
            href = el.attrib.get("href", "")
            manifest[item_id] = (href, el.attrib.get("media-type", ""), el.attrib.get("properties", ""))
        elif tag == "meta" and el.attrib.get("name") == "cover":
            cover_ids.add(el.attrib.get("content", ""))
        elif tag == "reference" and el.attrib.get("type", "").lower() == "cover":
            cover_ids.add(el.attrib.get("href", "").split("#")[0])

    out: list[tuple[str, bool]] = []
    for el in opf.iter():
        if _local(el.tag) != "itemref":
            continue
        idref = el.attrib.get("idref", "")
        entry = manifest.get(idref)
        if not entry:
            continue
        href, media_type, _props = entry
        if media_type and media_type not in _HTML_TYPES:
            continue
        member = posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0])))
        if member not in names:
            continue
        is_cover = (
            "cover" in idref.lower()
            or "cover" in posixpath.basename(href).lower()
            or idref in cover_ids
            or href in cover_ids
        )
        out.append((member, is_cover))
    return out


def epub_text_sample(path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            parts: list[str] = []
            substantial = 0
            scanned = 0
            for member, is_cover in _epub_spine(zf):
                if is_cover:
                    continue
                if scanned >= EPUB_MAX_DOCS_SCANNED:
                    break
                scanned += 1
                try:
                    raw = zf.read(member).decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001 - skip an unreadable member
                    continue
                text = _clean(_html_to_text(raw))
                if not text:
                    continue
                parts.append(text)
                if len(text) >= EPUB_DOC_MIN_CHARS:
                    substantial += 1
                if substantial >= EPUB_DOCS_WANTED or sum(len(p) for p in parts) >= max_chars:
                    break
    except Exception as exc:  # noqa: BLE001 - not a usable zip
        logger.info("text sample: could not read EPUB %s (%s)", path.name, exc)
        return None
    joined = "\n\n".join(parts).strip()
    return joined[:max_chars] or None


def pdf_text_sample(path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        try:
            pages = [doc[i].get_text() for i in range(min(PDF_PAGES, len(doc)))]
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        logger.info("text sample: could not read PDF %s (%s)", path.name, exc)
        return None
    joined = "\n\n".join(_clean(p) for p in pages if p and p.strip()).strip()
    return joined[:max_chars] or None


def first_pages_text(path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    """Plain text from the first pages, at most ``max_chars``, or None for
    formats without text (comics) and unreadable files."""
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return epub_text_sample(path, max_chars)
    if suffix == ".pdf":
        return pdf_text_sample(path, max_chars)
    return None
