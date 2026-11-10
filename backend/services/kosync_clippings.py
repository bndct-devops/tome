"""Clippings synced by a CrossInk reader (experimental).

CrossInk calls a passage saved while reading a "clipping" and syncs them over
the same extended KOSync API as its reading stats (routes in
``backend/api/kosync_stats.py``). Unlike stats, the sync goes both ways: the
reader uploads the clippings made on it, then pages through everything the
server has for the book and restores what it does not have, including
deletions made on another reader.

The contract, as the firmware uses it (CrossInk ``src/network/ClippingsUpload.cpp``):

- ``PUT .../clippings/{document}`` with ``{"items": [...]}``, one item per
  request: first ``{"id", "deleted": 1}`` for each deletion, then each
  clipping made on the device with its position fields and ``text`` but no
  ``note``/``color`` (those are kept as they are on the server). The reply is
  delivered only when it is ``{"until": <uint>, "accepted": 1}``; a delivered
  deletion is forgotten by the reader. Every sync sends all of the device's
  own clippings again, so a repeat must change nothing.
- ``GET .../clippings/{document}?cursor=N&limit=1&format=reader``, repeated
  with the returned ``cursor`` until ``more`` is false. The reply must carry
  ``sync_version: 2``, the ``document`` exactly as requested, ``cursor``,
  ``more`` and ``items``; an item's ``revision`` must equal the new cursor.
  ``format=reader`` leaves out ``note`` and ``color``.

Validation follows the reference server (crosspoint-sync
``src/routes/v1/clippings.ts``) with stricter bounds where the firmware would
otherwise refuse the clipping on the way back: position fields are 16-bit
(the reader reads them back as ``uint16``) and ``text`` may not contain a NUL
byte (the reader compares its length with the JSON string's). A refused batch
writes nothing. Ids are the reader's: 1-64 printable ASCII characters, unique
per Tome user and document. A deleted id stays deleted: a late upload of it is
acknowledged and dropped.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.kosync import KOSyncClipping
from backend.services.kosync_stats import Rejected

MAX_BATCH = 50
MAX_TEXT_BYTES = 4096
MAX_NOTE_BYTES = 4096
MAX_CHAPTER = 64
MAX_COLOR = 32
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
UINT16 = 0xFFFF
UINT32 = 0xFFFFFFFF
MAX_CURSOR = 2**53 - 1   # the reader refuses a cursor past JavaScript's safe integers

_ID = re.compile(r"^[\x21-\x7e]{1,64}$")
_DOCUMENT = re.compile(r"^[0-9a-fA-F]{32}$")
_POSITION_FIELDS = ("start_page", "end_page", "start_word", "end_word", "words")


def valid_document(document: str) -> bool:
    return bool(_DOCUMENT.match(document))


def _uint(value: Any, hi: int, default: Optional[int] = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= hi:
        raise Rejected("Invalid clipping")
    return value


def _utf8_len(value: str) -> int:
    """UTF-8 length, refusing strings that cannot be stored (lone surrogates)."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise Rejected("Invalid clipping") from None


@dataclass
class _Live:
    clip_id: str
    fields: dict[str, Any]          # every stored column the upload sets
    note: tuple[bool, Optional[str]]   # (sent, value)
    color: tuple[bool, Optional[str]]


@dataclass
class _Deletion:
    clip_id: str


def _parse(raw: Any) -> _Live | _Deletion:
    if not isinstance(raw, dict):
        raise Rejected("Invalid clipping")
    clip_id = raw.get("id")
    if not isinstance(clip_id, str) or not _ID.match(clip_id):
        raise Rejected("Invalid clipping id")
    if raw.get("deleted") == 1:  # 1 or true, as the reference server reads it
        return _Deletion(clip_id)

    if "spine" not in raw:
        raise Rejected("Invalid clipping")
    fields: dict[str, Any] = {"spine": _uint(raw.get("spine"), UINT16)}
    for name in _POSITION_FIELDS:
        fields[name] = _uint(raw.get(name, 0), UINT16)
    fields["pages"] = _uint(raw.get("pages", 1), UINT16)
    para = raw.get("para")
    fields["para"] = None if para is None else _uint(para, UINT16)
    fields["created_at"] = _uint(raw.get("created_at", 0), UINT32)
    fields["layout_signature"] = _uint(raw.get("layout_signature", 0), UINT32)
    start, end = raw.get("start_offset"), raw.get("end_offset")
    if (start is None) != (end is None):
        raise Rejected("Invalid clipping offsets")
    if start is not None:
        start, end = _uint(start, UINT32), _uint(end, UINT32)
        if end <= start:
            raise Rejected("Invalid clipping offsets")
    fields["start_offset"], fields["end_offset"] = start, end

    text = raw.get("text")
    if not isinstance(text, str) or not text or "\x00" in text or _utf8_len(text) > MAX_TEXT_BYTES:
        raise Rejected("Invalid clipping text")
    fields["text"] = text
    chapter = raw.get("chapter")
    chapter = chapter[:MAX_CHAPTER] if isinstance(chapter, str) else ""
    _utf8_len(chapter)
    fields["chapter"] = chapter

    note = raw.get("note")
    if isinstance(note, str):
        note = note if _utf8_len(note) <= MAX_NOTE_BYTES else None
    else:
        note = None
    color = raw.get("color")
    if isinstance(color, str):
        color = color[:MAX_COLOR]
        _utf8_len(color)
    else:
        color = None
    return _Live(clip_id, fields, ("note" in raw, note), ("color" in raw, color))


def parse_batch(body: dict[str, Any]) -> list[_Live | _Deletion]:
    items = body.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise Rejected(f"items must hold 1 to {MAX_BATCH} clippings")
    return [_parse(item) for item in items]


def _next_revision():
    """The next revision: the larger of MAX(revision)+1 and the current time in
    microseconds since the epoch.

    Computed inside the INSERT/UPDATE statement itself, so it is taken under
    SQLite's write lock (the unique index backs it up). MAX+1 alone is only
    unique among rows that still exist: once the rows holding the highest
    revisions go (an account deleted and its sync username linked to a new
    user, a restored backup), it would hand out revisions at or below a
    reader's saved cursor, and that reader would never see those changes.
    The reader ties its cursor to server URL + sync username + document, not
    to Tome's user. The clock term keeps the counter ahead of anything handed
    out earlier; the MAX+1 term keeps it strictly increasing when two changes
    land in one microsecond or the clock steps back. Microseconds stay far
    below the reader's 2^53-1 cursor limit (until the year 2255).
    """
    micros = time.time_ns() // 1000
    return select(
        func.max(func.coalesce(func.max(KOSyncClipping.revision), 0) + 1, micros)
    ).scalar_subquery()


def parse_item(raw: Any) -> _Live | _Deletion:
    """One clipping as the reader sends it, validated. Raises ``Rejected``."""
    return _parse(raw)


def store(db: Session, user_id: int, document: str, items: list[_Live | _Deletion],
          now: Optional[int] = None, commit: bool = True) -> int:
    """Apply a parsed batch. Returns how many items were accepted (all of them).
    ``commit=False`` leaves the commit to the caller."""
    now = int(time.time()) if now is None else now
    document = document.lower()
    for item in items:
        row = (
            db.query(KOSyncClipping)
            .filter(KOSyncClipping.user_id == user_id, KOSyncClipping.document == document,
                    KOSyncClipping.clip_id == item.clip_id)
            .first()
        )
        if isinstance(item, _Deletion):
            if row is not None and row.deleted:
                continue
            if row is None:
                row = KOSyncClipping(user_id=user_id, document=document, clip_id=item.clip_id)
                db.add(row)
            row.deleted = True
            row.text = row.note = row.color = None
            row.updated_at = now
            row.revision = _next_revision()
            db.flush()
            continue

        if row is not None and row.deleted:
            continue  # a deleted id is never resurrected
        values = dict(item.fields)
        if values["created_at"] == 0:
            values["created_at"] = row.created_at if row is not None else now
        sent_note, note = item.note
        sent_color, color = item.color
        values["note"] = note if sent_note else (row.note if row is not None else None)
        values["color"] = color if sent_color else (row.color if row is not None else None)
        if row is None:
            row = KOSyncClipping(user_id=user_id, document=document, clip_id=item.clip_id, deleted=False)
            db.add(row)
        elif all(getattr(row, k) == v for k, v in values.items()):
            continue  # the reader re-sends every clipping on every sync
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = now
        row.revision = _next_revision()
        db.flush()
    if commit:
        db.commit()
    return len(items)


def _parse_limit(raw: Optional[str]) -> int:
    try:
        value = float(raw) if raw is not None else DEFAULT_LIMIT
    except ValueError:
        return DEFAULT_LIMIT
    if value != value or value <= 0:  # NaN or not positive
        return DEFAULT_LIMIT
    return int(min(value, MAX_LIMIT))


def _parse_since(raw: Optional[str]) -> int:
    try:
        value = float(raw) if raw is not None else 0
    except ValueError:
        return 0
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        return 0
    return int(value)


def parse_cursor(raw: str) -> int:
    if not raw.isdigit() or not raw.isascii():
        raise Rejected("Invalid cursor")
    value = int(raw)
    if value > MAX_CURSOR:
        raise Rejected("Invalid cursor")
    return value


def item_out(row: KOSyncClipping, reader: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row.clip_id,
        "revision": row.revision,
        "spine": row.spine,
        "start_page": row.start_page,
        "end_page": row.end_page,
        "pages": row.pages,
        "start_word": row.start_word,
        "end_word": row.end_word,
        "words": row.words,
        "para": row.para,
        "chapter": row.chapter,
        "text": row.text,
    }
    if not reader:
        item["note"] = row.note
        item["color"] = row.color
    item.update({
        "created_at": row.created_at,
        "deleted": 1 if row.deleted else 0,
        "updated_at": row.updated_at,
        "layout_signature": row.layout_signature,
        "start_offset": row.start_offset,
        "end_offset": row.end_offset,
    })
    return item


def page(db: Session, user_id: int, document: str, *, cursor: Optional[int],
         since: Optional[str], limit: Optional[str], reader: bool) -> dict[str, Any]:
    """One page of a document's changes, oldest first, tombstones included.

    With ``cursor`` the page holds changes after that revision (what readers
    use); without it, changes after ``since`` (server epoch seconds), the
    reference server's legacy mode.
    """
    size = _parse_limit(limit)
    query = db.query(KOSyncClipping).filter(
        KOSyncClipping.user_id == user_id, KOSyncClipping.document == document.lower(),
    )
    if cursor is not None:
        query = query.filter(KOSyncClipping.revision > cursor).order_by(KOSyncClipping.revision)
    else:
        query = query.filter(KOSyncClipping.updated_at > _parse_since(since)).order_by(
            KOSyncClipping.updated_at, KOSyncClipping.clip_id)
    rows = query.limit(size + 1).all()
    more = len(rows) > size
    rows = rows[:size]
    return {
        "document": document,
        "sync_version": 2,
        "cursor": rows[-1].revision if rows else (cursor or 0),
        "until": rows[-1].updated_at if more else int(time.time()),
        "more": more,
        "items": [item_out(r, reader) for r in rows],
    }


# ── showing clippings in Tome ────────────────────────────────────────────────
# A clipping belongs to a book through its document's link, resolved here at
# read time. These are read-only in Tome: they are not KOReader annotations
# (no xPointer), so they are never part of the plugin's annotation sync or the
# web reader's list, and only the reader that made them can change them.

SOURCE = "crossink"
ID_PREFIX = "clipping:"


def visible_clippings(db: Session, user, book_id: Optional[int] = None) -> list[tuple[KOSyncClipping, Any]]:
    """The user's live clippings on books they can see, with the book.

    Only the user's own clippings, only through the user's own document links,
    only on books in a reading status (a file-less book counts: it is the
    user's own record of reading) that the visibility rules let them see.

    One book can be linked to several documents (a file synced under two
    hashes, or a CrossPoint Sync merge brought in by the import), and the same
    clipping has the same id under each, since the reader derives it from the
    clipping's time and text. Per book there is one row per clip id: a
    deletion under any of the book's documents hides it, otherwise the most
    recently updated copy is shown. The reader's own sync (``page``) is per
    document and is not affected.
    """
    from backend.core.permissions import book_visibility_filter
    from backend.models.book import READING_STATUSES, Book
    from backend.models.kosync import KOSyncDocumentMap

    query = (
        db.query(KOSyncClipping, Book)
        .join(KOSyncDocumentMap, (KOSyncDocumentMap.tome_user_id == KOSyncClipping.user_id)
              & (func.lower(KOSyncDocumentMap.document) == KOSyncClipping.document))
        .join(Book, Book.id == KOSyncDocumentMap.book_id)
        .filter(
            KOSyncClipping.user_id == user.id,
            Book.status.in_(READING_STATUSES),
            book_visibility_filter(db, user),
        )
    )
    if book_id is not None:
        query = query.filter(Book.id == book_id)
    winners: dict[tuple[int, str], tuple[KOSyncClipping, Any]] = {}
    deleted: set[tuple[int, str]] = set()
    for clip, book in query.order_by(KOSyncClipping.id).all():
        key = (book.id, clip.clip_id)
        if clip.deleted:
            deleted.add(key)
            continue
        held = winners.get(key)
        if held is None or (clip.updated_at, clip.revision) > (held[0].updated_at, held[0].revision):
            winners[key] = (clip, book)
    return [pair for key, pair in winners.items() if key not in deleted]


def _utcnow():
    """Now, in UTC (a function so tests can fix the date)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _viewer_zone(tz_offset: int, tz: Optional[str]):
    """The viewer's time zone: the IANA name when it is valid, else the fixed
    offset in JS getTimezoneOffset() minutes (0, i.e. UTC, when not sent)."""
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo

    from backend.services.reading_day import _valid_tz

    name = _valid_tz(tz)
    if name:
        return ZoneInfo(name)
    try:
        return timezone(-timedelta(minutes=tz_offset))
    except (ValueError, OverflowError):
        return timezone.utc


def local_datetime(epoch: int, tz_offset: int = 0, tz: Optional[str] = None) -> Optional[str]:
    """A clipping's UTC epoch as the 'YYYY-MM-DD HH:MM:SS' wall-clock string
    the highlights views use for KOReader's own (device-local) timestamps,
    in the viewer's time zone. Never the server's: that is UTC in Docker."""
    from datetime import datetime, timezone

    if not epoch:
        return None
    try:
        when = datetime.fromtimestamp(epoch, timezone.utc).astimezone(_viewer_zone(tz_offset, tz))
    except (OverflowError, OSError, ValueError):
        return None
    return when.strftime("%Y-%m-%d %H:%M:%S")


def viewer_now(tz_offset: int = 0, tz: Optional[str] = None) -> str:
    """Now as a wall-clock string in the viewer's time zone."""
    return _utcnow().astimezone(_viewer_zone(tz_offset, tz)).strftime("%Y-%m-%d %H:%M:%S")


def synced_at(clip: KOSyncClipping) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(clip.updated_at, timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def display_fields(clip: KOSyncClipping, tz_offset: int = 0, tz: Optional[str] = None) -> dict[str, Any]:
    """The fields the highlights views share with KOReader annotations."""
    return {
        "id": f"{ID_PREFIX}{clip.id}",
        "source": SOURCE,
        "read_only": True,
        "highlighted_text": clip.text,
        "note": clip.note,
        "chapter": clip.chapter or None,
        "color": clip.color,
        "datetime": local_datetime(clip.created_at, tz_offset, tz),
    }


def parse_display_id(value: str) -> Optional[int]:
    if value.startswith(ID_PREFIX) and value[len(ID_PREFIX):].isdigit():
        return int(value[len(ID_PREFIX):])
    return None


def has_clippings_for(db: Session, user_id: int, document: str) -> bool:
    """Has this user's reader synced any clipping (or deletion) for ``document``?"""
    return db.query(KOSyncClipping.id).filter(
        KOSyncClipping.user_id == user_id,
        KOSyncClipping.document == document.lower(),
    ).first() is not None
