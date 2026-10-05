"""KOReader sync code: reading data carried off an offline device as a QR code.

The TomeSync plugin renders everything it could not send - reading sessions,
positions and ratings that failed to POST while the device had no network - as
one or more QR codes on the e-ink screen. A phone (the Tome app or the web UI
with a camera) scans them and posts the raw pages here. The phone never decodes
the payload: it only reads the page header to know when it has collected the
whole set, so only the plugin and this module have to agree on the format.

Wire format
-----------
Each QR page is text: ``TSC1:<id>:<i>/<n>:<chunk>`` where ``id`` is a random
hex tag shared by every page of one code, ``i``/``n`` are 1-based page numbers,
and ``chunk`` is a slice of the base64 of the zlib-compressed JSON payload.
The payload uses short keys to keep codes small::

    {"v": 1, "id": "3f9a1c02", "dev": "Kindle", "t": <device epoch now>,
     "s": [{"b": <book_id>, "st": <epoch>, "en": <epoch>, "d": <seconds>,
            "ps": <0-1>, "pe": <0-1>, "pg": <pages turned>}, ...],
     "p": [{"b": <book_id>, "pc": <0-1>, "xp": "<xpointer|page>", "t": <epoch>}, ...],
     "r": [{"b": <book_id>, "r": <stars|null>, "rv": "<review|null>"}, ...]}

Semantics mirror the plugin's normal flush paths so a later Wi-Fi flush of the
same queue is harmless:

- Sessions carry the same dedup key the plugin sends (``book-start-device``),
  so a page scanned twice, or a queue flushed later over Wi-Fi, inserts nothing
  new. Progress from a session only ever advances (monotonic).
- Positions carry the device's timestamp of the write. A position older than
  the row on the server (the phone or web reader moved on) is kept as is;
  otherwise it is applied exactly like the device's own PUT.
- Ratings are last-write-wins, as on the device.
- ``t`` lets the server measure the device clock offset the same way the
  highlight sync does, shifting every stamp into server time when the device
  clock is clearly off.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from backend.core.permissions import user_can_see_book
from backend.core.ratings import validate_rating
from backend.models.book import Book
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.book_progress import apply_progress_to_status, upsert_position
from backend.services.hardcover_sync import nudge as hardcover_nudge
from backend.services.session_hygiene import is_suspect, notify_suspect_session

PREFIX = "TSC1"
MAX_PAGES = 40
# Decompressed payload cap: 50 sessions + positions + ratings is a few KB;
# anything near this is not a sync code.
MAX_PAYLOAD_BYTES = 256_000
# ``t`` is when the code was *shown*, not when it is scanned, so the server
# cannot tell a device clock that runs behind from a code scanned later (a
# photo posted the next morning). A device clock AHEAD of the server is
# unambiguous - a code cannot be shown in the future - and is corrected past
# the same small tolerance the annotation sync uses. A device clock BEHIND is
# only corrected when it is off by more than any plausible scan delay: an
# e-reader that lost its clock is years out, not hours.
CLOCK_AHEAD_TOLERANCE_S = 120
CLOCK_BEHIND_TOLERANCE_S = 2 * 86400

_PAGE_RE = re.compile(r"^TSC1:([0-9a-fA-F]{4,16}):(\d{1,3})/(\d{1,3}):([A-Za-z0-9+/=]*)$")


class SyncCodeError(ValueError):
    """A malformed or inconsistent set of pages. The message is user-facing."""


def _empty_table_as_list(v):
    # lua-rapidjson encodes an empty Lua table as ``{}``; treat it as no items.
    if isinstance(v, dict) and not v:
        return []
    return v


class CodeSession(BaseModel):
    b: int
    st: int
    en: Optional[int] = None
    d: Optional[int] = None
    ps: Optional[float] = None
    pe: Optional[float] = None
    pg: Optional[int] = None


class CodePosition(BaseModel):
    b: int
    pc: float
    xp: Optional[str] = None
    t: int


class CodeRating(BaseModel):
    b: int
    r: Optional[float] = None
    rv: Optional[str] = None


class CodePayload(BaseModel):
    v: int
    id: str
    dev: Optional[str] = None
    t: int
    s: list[CodeSession] = Field(default_factory=list)
    p: list[CodePosition] = Field(default_factory=list)
    r: list[CodeRating] = Field(default_factory=list)

    @field_validator("s", "p", "r", mode="before")
    @classmethod
    def _lists(cls, v):
        return _empty_table_as_list(v)


# ── Page assembly ─────────────────────────────────────────────────────────────

def parse_page(text: str) -> tuple[str, int, int, str]:
    """``TSC1:<id>:<i>/<n>:<chunk>`` -> (id, i, n, chunk)."""
    m = _PAGE_RE.match(text.strip())
    if not m:
        raise SyncCodeError("Not a Tome sync code")
    code_id, i, n, chunk = m.group(1).lower(), int(m.group(2)), int(m.group(3)), m.group(4)
    if n < 1 or n > MAX_PAGES or i < 1 or i > n:
        raise SyncCodeError("Not a Tome sync code")
    return code_id, i, n, chunk


def assemble(pages: list[str]) -> CodePayload:
    """Validate a set of scanned pages and decode them into a payload.

    Pages may arrive in any order and may repeat (a page scanned twice); what
    must hold is that they all belong to one code and every page is present.
    """
    if not pages:
        raise SyncCodeError("No pages scanned")
    if len(pages) > MAX_PAGES * 2:
        raise SyncCodeError("Too many pages")
    code_id: Optional[str] = None
    total: Optional[int] = None
    chunks: dict[int, str] = {}
    for text in pages:
        pid, i, n, chunk = parse_page(text)
        if code_id is None:
            code_id, total = pid, n
        elif pid != code_id or n != total:
            raise SyncCodeError("These pages belong to different sync codes")
        if i in chunks and chunks[i] != chunk:
            raise SyncCodeError("Page %d was read twice with different contents" % i)
        chunks[i] = chunk
    assert total is not None
    missing = [str(i) for i in range(1, total + 1) if i not in chunks]
    if missing:
        raise SyncCodeError("Missing page %s of %d" % (", ".join(missing), total))
    joined = "".join(chunks[i] for i in range(1, total + 1))
    return decode_payload(joined, expected_id=code_id)


def decode_payload(b64: str, expected_id: Optional[str] = None) -> CodePayload:
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise SyncCodeError("Corrupt sync code")
    d = zlib.decompressobj()
    try:
        data = d.decompress(raw, MAX_PAYLOAD_BYTES)
    except zlib.error:
        raise SyncCodeError("Corrupt sync code")
    if d.unconsumed_tail:
        raise SyncCodeError("Sync code is too large")
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SyncCodeError("Corrupt sync code")
    try:
        payload = CodePayload.model_validate(obj)
    except ValidationError:
        raise SyncCodeError("Corrupt sync code")
    if payload.v != 1:
        raise SyncCodeError("This sync code needs a newer Tome version")
    if expected_id is not None and payload.id.lower() != expected_id:
        raise SyncCodeError("Corrupt sync code")
    return payload


def encode_payload(payload: dict, chunk_size: int = 600) -> list[str]:
    """The plugin's side of the format, in Python - used by the tests and as
    the executable reference for the Lua encoder."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.b64encode(zlib.compress(data, 9)).decode("ascii")
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)] or [""]
    n = len(chunks)
    return [f"{PREFIX}:{payload['id']}:{i + 1}/{n}:{c}" for i, c in enumerate(chunks)]


# ── Applying ──────────────────────────────────────────────────────────────────

def _utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def clock_offset_seconds(device_now: int, server_now: Optional[datetime] = None) -> int:
    """Seconds to add to the device's stamps to land in server time; 0 when
    the difference is explained by tolerance or by the scan happening after
    the code was shown."""
    now = server_now or datetime.utcnow()
    offset = round((now - _utc(device_now)).total_seconds())
    if offset < 0 and -offset >= CLOCK_AHEAD_TOLERANCE_S:
        return offset
    if offset > 0 and offset >= CLOCK_BEHIND_TOLERANCE_S:
        return offset
    return 0


class _BookAgg:
    __slots__ = (
        "book", "sessions_added", "sessions_known", "seconds", "pages",
        "progress_before", "position", "rating", "rating_applied",
    )

    def __init__(self, book: Book, progress_before: Optional[float]):
        self.book = book
        self.sessions_added = 0
        self.sessions_known = 0
        self.seconds = 0
        self.pages = 0
        self.progress_before = progress_before
        self.position: Optional[str] = None   # "applied" | "kept"
        self.rating: Optional[float] = None
        self.rating_applied = False


def apply_payload(db: Session, user: User, payload: CodePayload) -> dict:
    """Write a decoded payload for ``user`` and return the overview the phone shows.

    Commits. Books the user cannot see (or that no longer exist) are counted
    under ``unknown_books`` and otherwise ignored.
    """
    offset = clock_offset_seconds(payload.t)
    dev = payload.dev or ""
    aggs: dict[int, _BookAgg] = {}
    unknown: set[int] = set()

    def agg_for(book_id: int) -> Optional[_BookAgg]:
        if book_id in aggs:
            return aggs[book_id]
        if book_id in unknown:
            return None
        book = db.get(Book, book_id)
        if not book or book.status != "active" or not user_can_see_book(db, user, book):
            unknown.add(book_id)
            return None
        status = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
            .first()
        )
        aggs[book_id] = _BookAgg(book, status.progress_pct if status else None)
        return aggs[book_id]

    # Sessions: same dedup key as the plugin's own POST, so a later Wi-Fi
    # flush of the same queue (or a second scan) adds nothing.
    for s in payload.s:
        a = agg_for(s.b)
        if a is None:
            continue
        uuid = f"{s.b}-{s.st}-{dev}"
        existing = db.query(ReadingSession).filter(ReadingSession.session_uuid == uuid).first()
        if existing:
            a.sessions_known += 1
            continue
        db.add(ReadingSession(
            user_id=user.id,
            book_id=s.b,
            started_at=_utc(s.st + offset),
            ended_at=_utc(s.en + offset) if s.en is not None else None,
            duration_seconds=s.d,
            progress_start=s.ps,
            progress_end=s.pe,
            pages_turned=s.pg,
            device=payload.dev,
            session_uuid=uuid,
        ))
        db.flush()
        if s.pe is not None:
            apply_progress_to_status(db, user_id=user.id, book_id=s.b, pct=s.pe)
        if is_suspect(s.d, s.pg):
            notify_suspect_session(db, user.id, a.book.title, s.d or 0)
        a.sessions_added += 1
        a.seconds += s.d or 0
        a.pages += s.pg or 0

    # Positions: the device's write time decides. Newer on the server (the
    # phone read on, the web reader moved) means the device's is stale.
    for p in payload.p:
        a = agg_for(p.b)
        if a is None:
            continue
        written = _utc(p.t + offset)
        row = (
            db.query(TomeSyncPosition)
            .filter(TomeSyncPosition.user_id == user.id, TomeSyncPosition.book_id == p.b)
            .first()
        )
        if row is not None and row.updated_at is not None and row.updated_at > written:
            a.position = "kept"
            continue
        pct = max(0.0, min(1.0, p.pc))
        upsert_position(db, user_id=user.id, book_id=p.b, percentage=pct,
                        progress=p.xp, device=payload.dev)
        apply_progress_to_status(db, user_id=user.id, book_id=p.b, pct=pct,
                                 monotonic=False, cfi=p.xp or None)
        a.position = "applied"

    # Ratings: last-write-wins, as the device's own PUT.
    nudge = False
    for r in payload.r:
        a = agg_for(r.b)
        if a is None:
            continue
        try:
            validate_rating(r.r)
        except Exception:
            continue
        row = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == r.b)
            .first()
        )
        if not row:
            row = UserBookStatus(user_id=user.id, book_id=r.b, status="unread")
            db.add(row)
        if r.r != row.rating:
            nudge = True
        row.rating = r.r
        row.rated_at = datetime.utcnow() if r.r is not None else None
        row.review = r.rv or None
        a.rating = r.r
        a.rating_applied = True

    db.commit()
    if nudge:
        hardcover_nudge()

    books = []
    for book_id, a in aggs.items():
        status = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
            .first()
        )
        books.append({
            "book_id": book_id,
            "title": a.book.title,
            "author": a.book.author,
            "series": a.book.series,
            "series_index": a.book.series_index,
            "has_cover": bool(a.book.cover_path),
            "sessions_added": a.sessions_added,
            "sessions_known": a.sessions_known,
            "seconds": a.seconds,
            "pages": a.pages,
            "progress_before": a.progress_before,
            "progress_after": status.progress_pct if status else None,
            "status": status.status if status else None,
            "position": a.position,
            "rating": a.rating if a.rating_applied else None,
        })
    books.sort(key=lambda b: (-(b["sessions_added"] + b["sessions_known"]), b["title"] or ""))

    return {
        "device": payload.dev,
        "code_id": payload.id,
        "generated_at": _utc(payload.t + offset).isoformat() + "Z",
        "clock_offset_seconds": offset,
        "sessions_added": sum(b["sessions_added"] for b in books),
        "sessions_known": sum(b["sessions_known"] for b in books),
        "positions_applied": sum(1 for b in books if b["position"] == "applied"),
        "positions_kept": sum(1 for b in books if b["position"] == "kept"),
        "ratings_applied": sum(1 for b in books if b["rating"] is not None),
        "unknown_books": len(unknown),
        "books": books,
    }
