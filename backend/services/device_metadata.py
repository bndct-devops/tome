"""Tome -> KOReader metadata projection (TomeSync "metadata sync").

The plugin asks for the metadata of books it holds on the device and writes
it into KOReader's own *custom metadata* sidecar (``custom_metadata.lua`` next
to the ``.sdr``), which is the same override layer a user fills in by hand via
Book information > Edit. The device file itself is never touched, so its
partial-MD5 identity, reading position and sidecar state all stay put.

Two contracts live here so both the endpoint and its tests share them:

* :func:`device_metadata` - the exact payload the plugin applies. Field names
  are KOReader's (``authors``, ``keywords``), not Tome's, so the plugin can
  copy them straight into ``custom_props`` without a mapping table of its own.
  Only fields Tome has a value for are sent: KOReader falls back to the
  file's embedded value for anything absent from ``custom_props``.
* :func:`metadata_fingerprint` - a content hash the plugin stores per file and
  sends back on the next run. The server answers "unchanged" when it matches,
  so a steady-state run moves no descriptions or covers over the wire. It is
  a hash of the projected *content*, not ``updated_at``: tag edits replace
  ``BookTag`` rows without touching the ``books`` row, and a re-uploaded
  cover keeps its filename, so a timestamp would miss both.
"""
from __future__ import annotations

import hashlib
import json
import os

from backend.core.config import settings
from backend.models.book import Book

# KOReader's BookInfo.props, in its order. Anything else is not customisable
# on the device and must not be sent.
KOREADER_PROPS = ("title", "authors", "series", "series_index", "language",
                  "keywords", "description")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _series_index(value: float | None) -> int | float | None:
    if value is None:
        return None
    # KOReader edits series_index as a number; 2.0 should read "2", not "2.0".
    return int(value) if float(value).is_integer() else float(value)


def _cover_stat(book: Book) -> tuple[int, int] | None:
    """(size, mtime_ns) of the cover file, or None when there is no cover.

    Part of the fingerprint so a cover replaced under the same filename still
    reads as a change.
    """
    if not book.cover_path:
        return None
    try:
        st = os.stat(settings.covers_dir / book.cover_path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def projected_props(book: Book) -> dict:
    """The ``custom_props`` KOReader should show for this book (no Nones)."""
    tags = sorted({t.tag.strip() for t in book.tags if t.tag and t.tag.strip()})
    props = {
        "title": _clean(book.title),
        "authors": _clean(book.author),
        "series": _clean(book.series),
        "series_index": _series_index(book.series_index) if _clean(book.series) else None,
        "language": _clean(book.language),
        # KOReader keeps multiple subjects newline-separated (crengine
        # convention); Book information renders them comma-joined.
        "keywords": "\n".join(tags) if tags else None,
        "description": _clean(book.description),
    }
    return {k: v for k, v in props.items() if v is not None}


def metadata_fingerprint(book: Book) -> str:
    cover = _cover_stat(book)
    material = json.dumps(
        [projected_props(book), book.cover_path if cover else None, cover],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def device_metadata(book: Book) -> dict:
    """Wire payload for one book: props + cover flag + fingerprint."""
    cover = _cover_stat(book) is not None
    return {
        "book_id": book.id,
        "fingerprint": metadata_fingerprint(book),
        "props": projected_props(book),
        "cover": cover,
    }
