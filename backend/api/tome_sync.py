"""TomeSync API — custom KOReader plugin endpoints.

Auth: Bearer API key (not JWT) for all /api/tome-sync/ endpoints.
Plugin download: Bearer JWT for /api/plugin/koreader.
"""
import calendar
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel as PydanticBaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.urls import public_base_url
from backend.core.permissions import book_visibility_filter, user_can_see_book
from backend.core.ratings import validate_rating
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.book import Book, BookFile, BookTag
from backend.models.library import Library, SavedFilter
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import Annotation, AnnotationTombstone, ApiKey, ReadingSession, TomeSyncPosition
from backend.models.ko_stats import StatsImport
from backend.models.send_queue import SendQueueItem
from backend.services.book_progress import apply_progress_to_status, upsert_position
from backend.services.hardcover_sync import nudge as hardcover_nudge
from backend.services.session_hygiene import is_suspect, notify_suspect_session
from backend.services.audit import audit

router = APIRouter(tags=["tome-sync"])
logger = logging.getLogger(__name__)

# Plugin versioning. BUILD is the ONLY value compared for self-update (monotonic
# integer — bump on every plugin code change). SEMVER is human-facing display.
# VERSION is kept as a back-compat alias (= str(BUILD)) for old plugins and the
# web UI, which read `version` from /plugin/version.
# BUILD 14: the HTTPS-sync fix also shipped as 1.2.1 (BUILD 13, cut from the
# v1.2.0 tag). main's impl carries more than 1.2.1's, so it must take a *higher*
# build than 13 — otherwise a device that updated to 1.2.1's build-13 impl and
# later points at a main/1.3.0 server (also 13) would not re-download main's
# richer impl. Hence 14.
# BUILD 31: half-star ratings — rating_baseline entries split into
# {remote, device} so a Tome half-star rounded onto the whole-star sidecar is
# never pushed back as a "local edit" (old {rating=...} entries migrate on read).
# BUILD 32: hygiene batch — clock-offset guard (device_time on annotation syncs;
# server-minted stamps shifted into the device frame; future-stamp clamps),
# pull-conflict strategy settings (forward/backward × prompt/silent/never), and
# a dedicated tomesync_state.lua for the data tables (migrated out of
# G_reader_settings, pruned for books no longer on disk).
# BUILD 36: shelves axis — the device browser gains a Shelves entry backed by
# GET /tome-sync/{shelves,shelf-books}; shelf params resolve server-side via
# the device-supported subset of the dashboard filters (drift-checked against
# /books in tests). Mixed lists download by each book's own identity.
# BUILD 33: catalog batch — device search (submit-based + recent searches),
# author browse axis, read-status write-back (hold a book row); the shared
# _bookListMenu drill-down shows per-book status markers. Server side: series
# list N+1 fixed, /tome-sync/{authors,author-books,search} + PUT status.
# BUILD 34: library sweep — "Sync closed books" walks the device for books
# TomeSync never synced, resolves them by partial-MD5 in batches
# (POST /tome-sync/match-hashes) and adopts each sidecar's status/rating/
# progress via the fill-gaps-only POST /tome-sync/sweep/{book_id}. Ack-gated
# ledger keyed by sidecar mtime → resumable, re-run-safe.
# BUILD 35: sync on suspend (issue #128) — opt-in catch-up of pending
# sessions/ratings + reading-history backfill when the device sleeps, with an
# aggressive variant that turns WiFi on first (turnOnWifiAndWaitForConnection,
# bypassing wifi_enable_action so "prompt" never dialogs onto the sleep
# screen). onNetworkConnected also runs the history backfill now (was
# launch-only) and no longer requires an open book to flush pending state.
# BUILD 39: "Sync now" pulls before it pushes (issue #175) — the server
# position runs through the same forward/backward pull-conflict strategy as
# book open, so a device that is behind no longer overwrites newer progress
# from another device; the push happens after the pull settles. Pairs with the
# server-side TOME_KOSYNC_POSITION_BRIDGE read bridge (experimental, off by
# default), which lets that pull also see third-party KOSync client pushes.
TOMESYNC_PLUGIN_BUILD = 40
TOMESYNC_PLUGIN_SEMVER = "1.13.0"
TOMESYNC_PLUGIN_VERSION = str(TOMESYNC_PLUGIN_BUILD)


# ── API key auth ──────────────────────────────────────────────────────────────

def _get_api_key_user(
    authorization: str = Header(..., description="Bearer <api_key>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    plaintext = authorization.removeprefix("Bearer ").strip()
    # Hash the incoming plaintext and look up by hash. Plaintext is never stored.
    key_hash = ApiKey.hash_key(plaintext)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    user = db.get(User, api_key.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    # Update last_used_at
    api_key.last_used_at = datetime.utcnow()
    db.commit()
    return user


def _get_position(db: Session, user_id: int, book_id: int) -> Optional[TomeSyncPosition]:
    return (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == user_id, TomeSyncPosition.book_id == book_id)
        .first()
    )


def _newest_kosync_progress(db: Session, user_id: int, book_id: int):
    """Newest KOSync push for this (Tome user, book), or None.

    Read side of the #175 interop bridge (TOME_KOSYNC_POSITION_BRIDGE): joins
    the user's document maps to KOSyncProgress through their linked KOSync
    account(s). Both stores are server-stamped, so comparing timestamps is
    free of device clock skew.
    """
    from backend.models.kosync import KOSyncDocumentMap, KOSyncProgress, KOSyncUser

    return (
        db.query(KOSyncProgress)
        .join(KOSyncUser, KOSyncProgress.user_id == KOSyncUser.id)
        .join(
            KOSyncDocumentMap,
            (KOSyncDocumentMap.document == KOSyncProgress.document)
            & (KOSyncDocumentMap.tome_user_id == KOSyncUser.user_id),
        )
        .filter(KOSyncUser.user_id == user_id, KOSyncDocumentMap.book_id == book_id)
        .order_by(KOSyncProgress.timestamp.desc())
        .first()
    )


# ── Filename → book resolution helpers ────────────────────────────────────────
# These back the heuristic fallbacks in resolve_book, which only fire for files
# that never passed through Tome (no content-hash, no exact path). A wrong guess
# there silently writes one book's progress and annotations onto another, so the
# matching is deliberately strict: it would rather return 404 (device keeps
# local-only tracking) than clobber the wrong book.

# Titles/stems shorter than this may match only by exact equality — a bare
# "It"/"Us"/"Go" substring otherwise swallows unrelated sideloaded filenames.
_MIN_HEURISTIC_LEN = 4


def _phrase_index(haystack: str, needle: str) -> int:
    """Index of needle in haystack where it is not glued to an alphanumeric on
    either side (a whole-phrase occurrence), or -1. Tolerant of punctuation in
    needle, unlike a naive ``\\b`` regex."""
    start = 0
    n = len(needle)
    while True:
        pos = haystack.find(needle, start)
        if pos == -1:
            return -1
        before = haystack[pos - 1] if pos > 0 else ""
        after = haystack[pos + n] if pos + n < len(haystack) else ""
        if not before.isalnum() and not after.isalnum():
            return pos
        start = pos + 1


def _title_in_stem(title_l: str, stem_l: str) -> bool:
    """Book title occurs as a whole phrase inside the filename stem. Short
    titles must equal the stem exactly, so "It" can't match "The Italian Job"."""
    if len(title_l) < _MIN_HEURISTIC_LEN:
        return title_l == stem_l
    return _phrase_index(stem_l, title_l) != -1


def _stem_in_title(stem_l: str, title_l: str) -> bool:
    """Filename stem is a word-boundaried fragment of the title (handles
    truncated names). Minimum length guards against "1" matching "1984"."""
    if len(stem_l) < _MIN_HEURISTIC_LEN:
        return False
    pos = title_l.find(stem_l)
    while pos != -1:
        before = title_l[pos - 1] if pos > 0 else ""
        if not before.isalnum():  # stem begins at a word boundary
            return True
        pos = title_l.find(stem_l, pos + 1)
    return False


# ── Resolve endpoint ─────────────────────────────────────────────────────────

@router.get("/tome-sync/resolve")
def resolve_book(
    filename: str,
    ko_md5: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Match a filename to a Tome book ID.

    Download paths name files differently — OPDS/single download use
    '{title}.ext', the series browser uses 'Vol. X — Title.ext', and a user
    naming template can produce e.g. '{series} - 02 - {title}.ext'. This is a
    best-effort fallback used only when the plugin has no cached id for the file,
    so it must never *guess* between two plausible books: a wrong guess silently
    writes one book's reading progress onto another (issue: vol-2 mapped to
    vol-1 because the series shared its name with its first book, so vol-1's
    title appeared inside vol-2's filename).
    """
    import re

    # 0. Deterministic identity: the device file's KOReader partial-MD5 against
    #    the hashes Tome recorded when it scanned or served the artifact. Exact
    #    however the file was renamed or moved on the device; everything below
    #    is heuristic fallback for files that never passed through Tome.
    if ko_md5:
        from backend.services.ko_hash import lookup_book_ids
        hit = lookup_book_ids(db, [ko_md5]).get(ko_md5)
        if hit is not None:
            book = db.get(Book, hit)
            if book and book.status == "active":
                return {"book_id": book.id, "method": "ko_hash"}

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem_l = stem.lower()

    # 1. Exact file path match in book_files — the only unambiguous signal.
    book_file = (
        db.query(BookFile)
        .filter(BookFile.file_path.endswith("/" + filename) | (BookFile.file_path == filename))
        .first()
    )
    if book_file:
        book = db.get(Book, book_file.book_id)
        if book and book.status == "active":
            return {"book_id": book.id}

    # Volume number, in any of the shapes the download paths emit:
    #   "Vol. 12", "Vol. 2.5", "v01", or a separator-delimited token
    #   "Series - 02 - Title". The separator token is bounded to 1-3 digits so a
    #   4-digit year ("Foo - 1984 - Bar") is not misread as volume 1984, and the
    #   "Vol." form accepts a half-volume (2.5) instead of truncating to 2.
    vol_num = None
    vol_match = (
        re.search(r'[Vv]ol\.?\s*(\d+(?:\.\d+)?)', stem)
        or re.search(r'\bv(\d{1,3})\b', stem)
        or re.search(r'[-—]\s*(\d{1,3})\s*[-—]', stem)
    )
    if vol_match:
        vol_num = float(vol_match.group(1))

    all_active = db.query(Book).filter(Book.status == "active").all()

    # 2. Forward match: the book's title appears as a whole phrase in the
    #    filename (short titles must match exactly — see _title_in_stem).
    candidates = [b for b in all_active if b.title and _title_in_stem(b.title.lower(), stem_l)]

    # When the filename carries a volume number it is authoritative: only a book
    # at that exact index may resolve. An index-less (standalone) candidate must
    # NOT win by substring — a numbered file does not belong to an unnumbered
    # book — and two books at the same index are genuinely ambiguous.
    if vol_num is not None:
        exact = [b for b in candidates if b.series_index == vol_num]
        if len(exact) == 1:
            return {"book_id": exact[0].id}
        raise HTTPException(
            status_code=404, detail="Ambiguous filename; could not resolve uniquely"
        )

    if len(candidates) == 1:
        return {"book_id": candidates[0].id}

    if candidates:
        # Most-specific title wins: the longest book title present in the filename
        # (e.g. "Dune Messiah" beats the "Dune" nested inside it). A tie is
        # genuinely ambiguous — refuse rather than clobber another book's progress.
        candidates.sort(key=lambda b: len(b.title or ""), reverse=True)
        if len(candidates[0].title) > len(candidates[1].title):
            return {"book_id": candidates[0].id}
        raise HTTPException(
            status_code=404, detail="Ambiguous filename; could not resolve uniquely"
        )

    # 3. Reverse fallback: the stem is a word-boundaried fragment of exactly one
    #    title (truncated names). Min length guards against "1" → "1984"; a
    #    volume number, if present, still requires an exact index match.
    reverse = [b for b in all_active if b.title and _stem_in_title(stem_l, b.title.lower())]
    if vol_num is not None:
        reverse = [b for b in reverse if b.series_index == vol_num]
    if len(reverse) == 1:
        return {"book_id": reverse[0].id}

    raise HTTPException(status_code=404, detail="No matching book found")


# ── Position endpoints ────────────────────────────────────────────────────────

@router.get("/tome-sync/position/{book_id}")
def get_position(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    pos = _get_position(db, user.id, book_id)

    # Experimental interop bridge (issue #175, TOME_KOSYNC_POSITION_BRIDGE):
    # serve the newest KOSync push when it beats the stored plugin/web
    # position — the mirror of kosync.py's GET, which serves the Tome position
    # to KOSync clients when that side is newer. Read-only: TomeSyncPosition
    # is never written from KOSync data, so the #156 write-side rule holds.
    # The locator travels as-is (a stock-KOReader xpointer resolves exactly on
    # the device; anything else makes the plugin fall back to the percentage).
    if settings.kosync_position_bridge:
        ks = _newest_kosync_progress(db, user.id, book_id)
        if ks is not None:
            pos_ts = (
                calendar.timegm(pos.updated_at.utctimetuple())
                if pos is not None and pos.updated_at is not None
                else None
            )
            if pos_ts is None or ks.timestamp > pos_ts:
                return {
                    "book_id": book_id,
                    "progress": ks.progress,
                    "percentage": ks.percentage,
                    "device": ks.device or "kosync",
                    "updated_at": datetime.utcfromtimestamp(ks.timestamp).isoformat() + "Z",
                }

    if not pos:
        raise HTTPException(status_code=404, detail="No position stored")

    return {
        "book_id": book_id,
        "progress": pos.progress,
        "percentage": pos.percentage,
        "device": pos.device,
        "updated_at": pos.updated_at.isoformat() + "Z",
    }


class PutPositionRequest(PydanticBaseModel):
    progress: Optional[str] = None
    percentage: float
    device: Optional[str] = None


@router.put("/tome-sync/position/{book_id}")
def put_position(
    book_id: int,
    body: PutPositionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    # Clamp percentage to 0-1 range
    pct = max(0.0, min(1.0, body.percentage))

    upsert_position(
        db, user_id=user.id, book_id=book_id,
        percentage=pct, progress=body.progress, device=body.device,
    )

    # Keep UserBookStatus in sync via the shared sticky-completion rule.
    # Position sync tracks the device last-write-wins (monotonic=False) — a
    # re-opened book CAN report lower progress; only completion is sticky.
    apply_progress_to_status(
        db, user_id=user.id, book_id=book_id, pct=pct,
        monotonic=False, cfi=body.progress or None,
    )

    db.commit()
    return {"ok": True, "timestamp": datetime.utcnow().isoformat() + "Z"}


# ── Rating endpoints ──────────────────────────────────────────────────────────
# The per-user star rating + review live on UserBookStatus, same as the web
# `/books/{id}/rating` and `/status` endpoints — but those authenticate via
# get_current_user (JWT / tome_ tokens) which does not accept the plugin's tk_
# API key. These mirror them under the api-key auth the plugin already uses for
# position/session, so KOReader's native rating can sync both ways.


@router.get("/tome-sync/rating/{book_id}")
def get_rating(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )
    return {
        "book_id": book_id,
        "rating": row.rating if row else None,
        "review": row.review if row else None,
    }


class PutRatingRequest(PydanticBaseModel):
    # The plugin only ever sends whole stars (KOReader's sidecar rating is an
    # int), but the field is float for symmetry with the web endpoint's
    # half-star steps.
    rating: Optional[float] = None  # 1–5 in half-star steps, or null to clear
    review: Optional[str] = None    # free-text, or null to clear


@router.put("/tome-sync/rating/{book_id}")
def put_rating(
    book_id: int,
    body: PutRatingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")
    validate_rating(body.rating)

    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )
    if not row:
        row = UserBookStatus(user_id=user.id, book_id=book_id, status="unread")
        db.add(row)

    # The plugin always sends both fields (nil → JSON null), so set both. A null
    # clears, matching the web endpoint's semantics.
    rating_changed = body.rating != row.rating
    row.rating = body.rating
    row.rated_at = datetime.utcnow() if body.rating is not None else None
    row.review = body.review or None
    db.commit()
    if rating_changed:
        hardcover_nudge()
    return {"ok": True, "rating": row.rating, "review": row.review}


# ── Session endpoint ──────────────────────────────────────────────────────────

class PostSessionRequest(PydanticBaseModel):
    book_id: int
    started_at: str  # ISO 8601
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    progress_start: Optional[float] = None
    progress_end: Optional[float] = None
    pages_turned: Optional[int] = None
    device: Optional[str] = None
    session_uuid: Optional[str] = None  # client dedup key


@router.post("/tome-sync/session", status_code=201)
def post_session(
    body: PostSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, body.book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    # Dedup: if same session_uuid already stored, return it
    if body.session_uuid:
        existing = (
            db.query(ReadingSession)
            .filter(ReadingSession.session_uuid == body.session_uuid)
            .first()
        )
        if existing:
            return {"session_id": existing.id}

    try:
        started = datetime.fromisoformat(body.started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(body.ended_at.replace("Z", "+00:00")) if body.ended_at else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {exc}")

    session = ReadingSession(
        user_id=user.id,
        book_id=body.book_id,
        started_at=started,
        ended_at=ended,
        duration_seconds=body.duration_seconds,
        progress_start=body.progress_start,
        progress_end=body.progress_end,
        pages_turned=body.pages_turned,
        device=body.device,
        session_uuid=body.session_uuid,
    )
    db.add(session)

    # Keep UserBookStatus in sync — catches up when position PUTs failed but
    # queued sessions flush later. Shared sticky-completion rule; monotonic:
    # a flushed session only ever advances progress, never lowers it.
    if body.progress_end is not None:
        apply_progress_to_status(
            db, user_id=user.id, book_id=body.book_id, pct=body.progress_end,
        )

    # Old plugin builds (< 37) book wall-clock time when a device sits awake
    # unread — flag implausible sessions so the user can trim them (#150).
    if is_suspect(body.duration_seconds, body.pages_turned):
        notify_suspect_session(db, user.id, book.title, body.duration_seconds or 0)

    db.commit()
    db.refresh(session)
    return {"session_id": session.id}


# ── KOReader statistics.sqlite3 import ────────────────────────────────────────
# Backfills the user's full KOReader reading history (page-level dwell data) into
# Tome. Books are matched to Tome books by filename (exact) or fuzzy title+series+
# volume. Idempotent: re-uploading the same rows is a no-op. See
# backend/services/ko_stats_import.py and docs/plans/stats-expansion-plan.md.

class KoStatBookItem(PydanticBaseModel):
    ko_id: int                       # KOReader's local book.id (joins page_stats below)
    md5: str = ""                    # KOReader partial md5 (stable per-device identity)
    title: str = ""
    authors: Optional[str] = None
    series: Optional[str] = None
    filename: Optional[str] = None   # device file path, when the book is still present
    pages: Optional[int] = None              # KOReader book.pages (total)
    total_read_pages: Optional[int] = None   # KOReader book.total_read_pages (distinct read)


class KoStatPageItem(PydanticBaseModel):
    ko_id: int
    page: int
    start_time: int                  # epoch seconds
    duration: int = 0
    total_pages: int = 0


class StatsImportRequest(PydanticBaseModel):
    device: str = ""
    books: list[KoStatBookItem]
    page_stats: list[KoStatPageItem]


@router.get("/tome-sync/stats/watermark")
def ko_stats_watermark(
    device: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Last synced page_stat start_time for this device, so the plugin uploads only newer rows."""
    wm = (
        db.query(StatsImport)
        .filter(StatsImport.user_id == user.id, StatsImport.device == device)
        .first()
    )
    return {"device": device, "last_start_time_synced": wm.last_start_time_synced if wm else 0}


@router.post("/tome-sync/stats/import", status_code=201)
def import_ko_stats(
    body: StatsImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    from backend.services.ko_stats_import import import_batch
    return import_batch(
        db, user,
        device=body.device,
        books=[b.model_dump() for b in body.books],
        page_stats=[p.model_dump() for p in body.page_stats],
    )


class RenameDeviceRequest(PydanticBaseModel):
    from_device: str
    to_device: str


@router.post("/tome-sync/stats/rename-device")
def rename_ko_stats_device(
    body: RenameDeviceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Carry a device's imported history + sync watermark over to the name it
    now reports (plugin build 40 moved every device off the literal "KOReader";
    Settings > Device name renames later). See `ko_stats_import.rename_device`."""
    from backend.services.ko_stats_import import rename_device
    result = rename_device(db, user, from_device=body.from_device, to_device=body.to_device)
    if result["rows_relabelled"] or result["watermark_moved"]:
        audit(db, "ko_stats.device_renamed", user_id=user.id, username=user.username,
              resource_type="ko_page_stats", resource_id=user.id,
              details={"from": body.from_device, "to": body.to_device, **result})
    return result


# ── Annotation endpoints ──────────────────────────────────────────────────────
# Bidirectional across KOReader devices. Identity is the anchor (xPointer). Edit
# conflicts resolve last-write-wins by the KOReader modification time; deletes are
# recorded as tombstones so a stale device can't resurrect a removed highlight.
# Timestamps are KOReader wall-clock strings ("YYYY-MM-DD HH:MM:SS") — they sort
# lexicographically = chronologically, so plain string compare gives LWW ordering.
# (Cross-device clock skew is a documented edge; acceptable for highlight notes.)

class AnnotationItem(PydanticBaseModel):
    anchor: str                          # KOReader pos0 (xPointer) or a stable fallback
    anchor_end: Optional[str] = None     # pos1 (xPointer) — lets another device render it
    highlighted_text: Optional[str] = None
    note: Optional[str] = None
    chapter: Optional[str] = None
    color: Optional[str] = None
    datetime: Optional[str] = None           # KOReader creation time
    datetime_updated: Optional[str] = None   # KOReader modification time (LWW key)
    # Set when this upsert is a device ADOPTING a web-created annotation: the
    # provisional "web:<uuid>" anchor this item replaces. The server drops the
    # provisional row (no tombstone — it's an identity move, not a delete).
    # Anchors are deterministic per book copy, so two devices adopting the same
    # web annotation produce the same real anchor and dedupe on upsert.
    adopted_from: Optional[str] = None

    @property
    def mtime(self) -> str:
        return self.datetime_updated or self.datetime or ""


class DeletedAnchor(PydanticBaseModel):
    anchor: str
    datetime: Optional[str] = None           # client deletion time (LWW key)


class SyncAnnotationsRequest(PydanticBaseModel):
    upserts: list[AnnotationItem] = []
    deletes: list[DeletedAnchor] = []
    # Device wall-clock at request time ("%Y-%m-%d %H:%M:%S"). Lets the server
    # compute this device's clock offset and shift server-minted LWW stamps into
    # the device's frame — see _clock_offset_seconds.
    device_time: Optional[str] = None

    # KOReader's Lua rapidjson encodes an empty table as a JSON object ({}), not an
    # array. Coerce that back to an empty list so an empty upserts/deletes is valid.
    @field_validator("upserts", "deletes", mode="before")
    @classmethod
    def _empty_obj_to_list(cls, v):
        return [] if v in (None, {}) else v


# ── Clock-offset guard ────────────────────────────────────────────────────────
# Annotation LWW stamps are plain wall-clock strings compared lexicographically.
# Stamps a DEVICE minted are in that device's frame (cross-device skew is a
# documented, accepted edge). Stamps the SERVER minted (web create/edit/delete)
# are in the server's frame — and a server clock ahead of a device makes those
# stamps land in the device's *future*, silently outranking every later local
# edit until the device clock catches up. Fix: the device stamps its wall-clock
# on sync requests; the server shifts every server-minted stamp into the
# device's frame, both in comparisons and in the response it returns.

_KO_DT_FMT = "%Y-%m-%d %H:%M:%S"
# Below this, treat the clocks as synchronized: request latency and second
# truncation produce small spurious offsets, and shifting by them would churn
# stamps for correctly-configured setups.
_CLOCK_OFFSET_TOLERANCE_S = 120


def _clock_offset_seconds(device_time: Optional[str]) -> int:
    """Seconds the server clock is AHEAD of the device clock (0 = in sync)."""
    if not device_time:
        return 0
    try:
        dev = datetime.strptime(device_time.strip()[:19], _KO_DT_FMT)
    except ValueError:
        return 0
    offset = round((datetime.now() - dev).total_seconds())
    return offset if abs(offset) >= _CLOCK_OFFSET_TOLERANCE_S else 0


def _shift_ko_dt(stamp: Optional[str], seconds: int) -> Optional[str]:
    """Shift a KOReader wall-clock string by N seconds; unparseable → unchanged."""
    if not stamp or not seconds:
        return stamp
    try:
        base = datetime.strptime(stamp.strip()[:19], _KO_DT_FMT)
    except ValueError:
        return stamp
    return (base + timedelta(seconds=seconds)).strftime(_KO_DT_FMT)


def _serialize_annotation(a: Annotation, offset: int = 0) -> dict:
    # Server-minted stamps travel to the device in the DEVICE's clock frame.
    shift = -offset if a.server_minted else 0
    return {
        "id": a.id,
        "anchor": a.anchor,
        "anchor_end": a.anchor_end,
        "highlighted_text": a.highlighted_text,
        "note": a.note,
        "chapter": a.chapter,
        "color": a.color,
        "datetime": _shift_ko_dt(a.koreader_datetime, shift),
        "datetime_updated": _shift_ko_dt(a.koreader_datetime_updated, shift),
        "updated_at": a.updated_at.isoformat() + "Z",
    }


def _annotation_state(db: Session, user_id: int, book_id: int):
    """Current alive annotations + tombstones for a user+book, keyed by anchor."""
    alive = {
        a.anchor: a
        for a in db.query(Annotation)
        .filter(Annotation.user_id == user_id, Annotation.book_id == book_id)
        .all()
    }
    tombs = {
        t.anchor: t
        for t in db.query(AnnotationTombstone)
        .filter(AnnotationTombstone.user_id == user_id, AnnotationTombstone.book_id == book_id)
        .all()
    }
    return alive, tombs


def _annotation_response(db: Session, user_id: int, book_id: int, offset: int = 0, **extra) -> dict:
    alive, tombs = _annotation_state(db, user_id, book_id)
    rows = sorted(alive.values(), key=lambda a: (a.koreader_datetime or "", a.id))
    return {
        "book_id": book_id,
        "annotations": [_serialize_annotation(a, offset) for a in rows],
        "tombstones": [
            {
                "anchor": t.anchor,
                "deleted_at": _shift_ko_dt(t.client_deleted_at, -offset if t.server_minted else 0),
            }
            for t in tombs.values()
        ],
        "server_time": datetime.now().strftime(_KO_DT_FMT),
        **extra,
    }


@router.post("/tome-sync/annotations/{book_id}/sync")
def sync_annotations(
    book_id: int,
    body: SyncAnnotationsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Merge a device's annotation changes and return the full reconciled state.

    Upserts win over an existing row / tombstone only when strictly newer (LWW).
    Deletes drop the row and write a tombstone, unless a newer live edit exists.
    The response (alive set + tombstones) is what the device applies locally.
    """
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    alive, tombs = _annotation_state(db, user.id, book_id)
    created = updated = deleted = skipped = 0

    # Server clock minus device clock; server-minted stamps are compared (and
    # returned) in the device's frame so a fast server clock can't make web
    # actions permanently outrank the device's next local edit.
    offset = _clock_offset_seconds(body.device_time)

    def in_device_frame(stamp: Optional[str], minted: bool) -> str:
        return _shift_ko_dt(stamp, -offset if minted else 0) or ""

    for item in body.upserts:
        if not item.anchor:
            continue
        tomb = tombs.get(item.anchor)
        # A re-add only wins over a delete if it's strictly newer than the delete.
        if tomb and item.mtime <= in_device_frame(tomb.client_deleted_at, tomb.server_minted):
            skipped += 1
            continue
        if tomb:
            db.delete(tomb); tombs.pop(item.anchor, None)
        row = alive.get(item.anchor)
        if row:
            if item.mtime >= in_device_frame(row.effective_mtime, row.server_minted):  # newer edit wins
                row.anchor_end = item.anchor_end or row.anchor_end
                row.highlighted_text = item.highlighted_text
                row.note = item.note
                row.chapter = item.chapter
                row.color = item.color
                row.koreader_datetime = item.datetime or row.koreader_datetime
                row.koreader_datetime_updated = item.mtime or row.koreader_datetime_updated
                row.server_minted = False   # stamp is now device-authored
                updated += 1
            else:
                skipped += 1
        else:
            row = Annotation(
                user_id=user.id, book_id=book_id, anchor=item.anchor,
                anchor_end=item.anchor_end,
                highlighted_text=item.highlighted_text, note=item.note,
                chapter=item.chapter, color=item.color,
                koreader_datetime=item.datetime, koreader_datetime_updated=item.mtime or None,
            )
            db.add(row); alive[item.anchor] = row
            created += 1
        # Adoption: the device located a web-created annotation in the book and
        # re-anchored it natively — retire the provisional row. Runs regardless
        # of the upsert outcome (another device may have adopted first; the
        # canonical row then already exists and only the cleanup matters).
        if item.adopted_from and item.adopted_from.startswith("web:"):
            provisional = alive.get(item.adopted_from)
            if provisional is not None:
                db.delete(provisional)
                alive.pop(item.adopted_from, None)

    for d in body.deletes:
        if not d.anchor:
            continue
        row = alive.get(d.anchor)
        # If a live edit is newer than this delete, the edit wins — keep it.
        if row and in_device_frame(row.effective_mtime, row.server_minted) > (d.datetime or ""):
            skipped += 1
            continue
        if row:
            db.delete(row); alive.pop(d.anchor, None)
            deleted += 1
        tomb = tombs.get(d.anchor)
        if tomb:
            if (d.datetime or "") > in_device_frame(tomb.client_deleted_at, tomb.server_minted):
                tomb.client_deleted_at = d.datetime
                tomb.server_minted = False   # stamp is now device-authored
        else:
            db.add(AnnotationTombstone(
                user_id=user.id, book_id=book_id, anchor=d.anchor,
                client_deleted_at=d.datetime,
            ))

    db.commit()
    return _annotation_response(
        db, user.id, book_id, offset=offset,
        applied={"created": created, "updated": updated, "deleted": deleted, "skipped": skipped},
    )


@router.get("/tome-sync/annotations/{book_id}")
def get_annotations_plugin(
    book_id: int,
    device_time: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Full annotation state (alive + tombstones) for this user+book — what the
    plugin pulls and merges on book open."""
    return _annotation_response(db, user.id, book_id, offset=_clock_offset_seconds(device_time))


# ── Series endpoints (API-key-authed, for the plugin) ────────────────────────

def _status_map(db: Session, user_id: int, book_ids: list[int]) -> dict[int, str]:
    """book_id -> reading status for this user, one query."""
    if not book_ids:
        return {}
    rows = (
        db.query(UserBookStatus.book_id, UserBookStatus.status)
        .filter(UserBookStatus.user_id == user_id, UserBookStatus.book_id.in_(book_ids))
        .all()
    )
    return {bid: status for bid, status in rows if status}


def _book_entry(b: Book, status_map: dict[int, str]) -> dict:
    """The book shape the plugin's volume-list menus consume. `series` and
    `status` are additive (build 33+); older plugins ignore them."""
    entry = {
        "id": b.id,
        "title": b.title,
        "series_index": b.series_index,
        "author": b.author,
        "book_type": b.book_type.slug if b.book_type else None,
        "status": status_map.get(b.id, "unread"),
        "files": [
            {"id": f.id, "format": f.format, "file_size": f.file_size}
            for f in b.files
        ],
    }
    # Only include series when real — JSON null crashes the KOReader menus
    # (rapidjson decodes it to a truthy userdata sentinel).
    if b.series:
        entry["series"] = b.series
    return entry


@router.get("/tome-sync/series")
def list_series(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """List all series for the series browser menu."""
    # Only count/expose books this user is allowed to see. book_visibility_filter
    # uses correlated EXISTS subqueries (no joins), so it doesn't duplicate rows
    # under the group_by, and returns True (no-op) for admins.
    #
    # One query for everything: ordered by (series, series_index, title), the
    # first row seen per series IS its first book — the old per-series
    # first_book query was an N+1 that scaled with the library.
    visibility = book_visibility_filter(db, user)
    rows = (
        db.query(Book.series, Book.id, Book.author)
        .filter(Book.status == "active", Book.series.isnot(None), visibility)
        .order_by(Book.series.asc(),
                  Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )

    result = []
    by_name: dict[str, dict] = {}
    for series_name, book_id, author in rows:
        entry = by_name.get(series_name)
        if entry is None:
            entry = {"name": series_name, "book_count": 0, "first_book_id": book_id}
            # Only include author when it's a real string. Emitting JSON null here
            # crashes the KOReader series browser, because rapidjson decodes null to
            # a (truthy) userdata sentinel that the plugin then tries to concatenate.
            if author:
                entry["author"] = author
            by_name[series_name] = entry
            result.append(entry)
        entry["book_count"] += 1

    # Append the unserialized bucket last, mirroring backend/api/books.py, so the
    # plugin's series browser exposes a single "No Series" entry through which
    # standalone books can be browsed and downloaded.
    unserialized = (
        db.query(Book.id)
        .filter(Book.status == "active", Book.series.is_(None), visibility)
        .order_by(Book.id)
        .all()
    )
    if unserialized:
        result.append({
            "name": "__unserialized__",
            "book_count": len(unserialized),
            "first_book_id": unserialized[0][0],
        })

    return result


@router.get("/tome-sync/authors")
def list_authors(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """List authors for the plugin's author browse axis — the natural way into
    standalone books, which the series browser lumps into one No Series bucket."""
    visibility = book_visibility_filter(db, user)
    rows = (
        db.query(Book.author, func.count(Book.id))
        .filter(Book.status == "active", Book.author.isnot(None), Book.author != "", visibility)
        .group_by(Book.author)
        .order_by(Book.author.asc())
        .all()
    )
    result = [{"name": name, "book_count": count} for name, count in rows]
    unknown = (
        db.query(func.count(Book.id))
        .filter(Book.status == "active",
                (Book.author.is_(None)) | (Book.author == ""), visibility)
        .scalar()
    )
    if unknown:
        result.append({"name": "__unknown__", "book_count": unknown})
    return result


@router.get("/tome-sync/author-books")
def get_author_books(
    author: str,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """All of one author's visible books, shaped like a series volume list so
    the plugin reuses the same drill-down menu. Query param (not a path
    segment): author names contain slashes, dots, and everything else."""
    visibility = book_visibility_filter(db, user)
    if author == "__unknown__":
        author_filter = (Book.author.is_(None)) | (Book.author == "")
    else:
        author_filter = Book.author == author
    books = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.book_type))
        .filter(Book.status == "active", author_filter, visibility)
        .order_by(Book.series.asc().nullslast(),
                  Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )
    smap = _status_map(db, user.id, [b.id for b in books])
    return {"author": author, "books": [_book_entry(b, smap) for b in books]}


# ── Shelves on the device (build 36) ─────────────────────────────────────────

# Shelf params resolve through the shared resolver (also used by public
# share links) — see backend/services/shelf_resolver.py.
from backend.services.shelf_resolver import shelf_query as _shelf_query  # noqa: E402


@router.get("/tome-sync/shelves")
def get_shelves(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """The user's shelves with resolved book counts, for the device browser."""
    from sqlalchemy import distinct as sa_distinct

    rows = (
        db.query(SavedFilter)
        .filter(SavedFilter.owner_id == user.id)
        .order_by(SavedFilter.sort_order, SavedFilter.name)
        .all()
    )
    out = []
    for sf in rows:
        try:
            params = json.loads(sf.params or "{}")
        except ValueError:
            params = {}
        query, _unsup = _shelf_query(db, user, params)
        count = query.with_entities(func.count(sa_distinct(Book.id))).scalar() or 0
        out.append({"id": sf.id, "name": sf.name, "book_count": int(count)})
    return out


@router.get("/tome-sync/shelf-books")
def get_shelf_books(
    shelf_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """One shelf's visible books, shaped like the series drill-down list."""
    sf = db.get(SavedFilter, shelf_id)
    if not sf or sf.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Shelf not found")
    try:
        params = json.loads(sf.params or "{}")
    except ValueError:
        params = {}
    query, unsupported = _shelf_query(db, user, params)
    books = (
        query.order_by(Book.series.asc().nullslast(),
                       Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )
    # Joins (tag/format/library) can fan rows out — dedupe preserving order.
    seen: set[int] = set()
    unique = [b for b in books if not (b.id in seen or seen.add(b.id))]
    smap = _status_map(db, user.id, [b.id for b in unique])
    return {
        "shelf": sf.name,
        "unsupported_filters": unsupported,
        "books": [_book_entry(b, smap) for b in unique],
    }


@router.get("/tome-sync/search")
def search_books(
    q: str,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Free-text search over title/author/series for the plugin (LIKE terms,
    all must match). Same book shape as the series drill-down."""
    terms = [t for t in q.strip().split() if t]
    if not terms:
        return {"query": q, "total": 0, "books": []}
    visibility = book_visibility_filter(db, user)
    query = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.book_type))
        .filter(Book.status == "active", visibility)
    )
    for t in terms:
        like = f"%{t}%"
        query = query.filter(
            Book.title.ilike(like) | Book.author.ilike(like) | Book.series.ilike(like)
        )
    total = query.count()
    books = (
        query.order_by(Book.series.asc().nullslast(),
                       Book.series_index.asc().nullslast(), Book.title.asc())
        .limit(50)
        .all()
    )
    smap = _status_map(db, user.id, [b.id for b in books])
    return {"query": q, "total": total, "books": [_book_entry(b, smap) for b in books]}


class StatusUpdate(PydanticBaseModel):
    status: str


@router.put("/tome-sync/status/{book_id}")
def put_reading_status(
    book_id: int,
    body: StatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Set unread/reading/read from the device's volume list (write-back).

    A deliberate user action, so it writes status directly — unlike telemetry
    (position/stats), which only ever *suggests* status via the sticky rule."""
    if body.status not in ("unread", "reading", "read"):
        raise HTTPException(status_code=422, detail="status must be unread|reading|read")
    book = db.get(Book, book_id)
    if not book or book.status != "active" or not user_can_see_book(db, user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )
    if row is None:
        row = UserBookStatus(user_id=user.id, book_id=book_id)
        db.add(row)
    row.status = body.status
    db.commit()
    return {"ok": True, "book_id": book_id, "status": body.status}


# ── Library sweep — closed books (build 34) ──────────────────────────────────
# The plugin walks the device library for books it has NEVER synced (pre-Tome
# history, other launchers, sideloads), resolves them by partial-MD5 in
# batches, then adopts each sidecar's state. Adoption is FILL GAPS ONLY: a
# closed book's stale sidecar must never overwrite state the live sync (or the
# user, on the web) already owns.

class MatchHashesRequest(PydanticBaseModel):
    hashes: list[str] = []

    # KOReader's Lua rapidjson encodes an empty table as a JSON object.
    @field_validator("hashes", mode="before")
    @classmethod
    def _empty_obj_to_list(cls, v):
        return [] if v in (None, {}) else v


@router.post("/tome-sync/match-hashes")
def match_hashes(
    body: MatchHashesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Batch partial-MD5 → book id resolution (max 500 per call). Only books
    this user can see are returned — an unmatched hash is indistinguishable
    from an invisible book, by design."""
    from backend.services.ko_hash import lookup_book_ids

    hashes = [h for h in body.hashes if isinstance(h, str) and h][:500]
    found = lookup_book_ids(db, hashes)
    if not found:
        return {"matches": {}}
    visible = {
        r[0]
        for r in db.query(Book.id)
        .filter(
            Book.id.in_(set(found.values())),
            Book.status == "active",
            book_visibility_filter(db, user),
        )
        .all()
    }
    return {"matches": {m: bid for m, bid in found.items() if bid in visible}}


class SweepBody(PydanticBaseModel):
    status: Optional[str] = None       # reading | read (sidecar summary.status)
    rating: Optional[float] = None     # 0.5–5.0 half-star steps
    review: Optional[str] = None
    percentage: Optional[float] = None  # 0..1 (sidecar percent_finished)
    progress: Optional[str] = None      # last xpointer
    device: Optional[str] = None


@router.post("/tome-sync/sweep/{book_id}")
def sweep_book(
    book_id: int,
    body: SweepBody,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Adopt a closed book's sidecar state, filling only what the server does
    not already have:

    - status: applied only over unread/absent — never downgrades curation
    - rating/review: applied only when no rating exists
    - position/progress: created only when NO position row exists — an actively
      synced book keeps its live position untouched

    Returns which fields were actually taken so the plugin can report honestly.
    """
    book = db.get(Book, book_id)
    if not book or book.status != "active" or not user_can_see_book(db, user, book):
        raise HTTPException(status_code=404, detail="Book not found")

    applied: dict = {}
    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )

    def ensure_row() -> UserBookStatus:
        nonlocal row
        if row is None:
            row = UserBookStatus(user_id=user.id, book_id=book_id, status="unread")
            db.add(row)
        return row

    if body.status in ("reading", "read"):
        r = ensure_row()
        if r.status in (None, "", "unread"):
            r.status = body.status
            applied["status"] = body.status

    if body.rating is not None:
        validate_rating(body.rating)
        r = ensure_row()
        if r.rating is None:
            r.rating = body.rating
            r.rated_at = datetime.utcnow()
            applied["rating"] = body.rating
            if body.review and not r.review:
                r.review = body.review
                applied["review"] = True

    if body.percentage is not None:
        pct = min(max(float(body.percentage), 0.0), 1.0)
        r = ensure_row()
        if r.progress_pct is None:
            r.progress_pct = pct
            applied["progress_pct"] = pct
        existing_pos = (
            db.query(TomeSyncPosition)
            .filter(TomeSyncPosition.user_id == user.id, TomeSyncPosition.book_id == book_id)
            .first()
        )
        if existing_pos is None:
            db.add(TomeSyncPosition(
                user_id=user.id, book_id=book_id,
                progress=body.progress, percentage=pct,
                device=body.device or "sweep",
            ))
            applied["position"] = True

    db.commit()
    if "rating" in applied:
        hardcover_nudge()
    return {"ok": True, "book_id": book_id, "applied": applied}


@router.get("/tome-sync/series/{book_id}")
def get_series_books(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Given a book_id, return all books in the same series with file info."""
    book = db.get(Book, book_id)
    if not book or book.status != "active" or not user_can_see_book(db, user, book):
        raise HTTPException(status_code=404, detail="Book not found")

    if book.series:
        series_filter = Book.series == book.series
        series_name = book.series
    else:
        # A book with no series resolves to the whole "No Series" bucket, so the
        # plugin can list and download standalone titles individually.
        series_filter = Book.series.is_(None)
        series_name = "__unserialized__"

    books = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.book_type))
        .filter(Book.status == "active", series_filter, book_visibility_filter(db, user))
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )

    # Series-level type, kept for backwards compatibility with older plugin
    # builds. It's representative for a real series (all volumes share a type)
    # but NOT for the "__unserialized__" bucket, which aggregates standalone
    # books of mixed types — so each book also carries its own `book_type`
    # below, which newer plugins prefer when filing downloads.
    book_type_slug = books[0].book_type.slug if books and books[0].book_type else "book"

    smap = _status_map(db, user.id, [b.id for b in books])
    return {
        "series_name": series_name,
        "book_type": book_type_slug,
        "books": [_book_entry(b, smap) for b in books],
    }


# ── Send-to-KOReader inbox (beta) ─────────────────────────────────────────────

@router.get("/tome-sync/inbox")
def get_inbox(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Pending 'Send to KOReader' items for this user. The plugin shows the
    count as a badge, downloads each book (filing it by series/author like the
    series browser), then POSTs `.../delivered`. Returns 404 when the feature is
    off, so the plugin hides the inbox entirely."""
    if not settings.send_to_koreader:
        raise HTTPException(status_code=404, detail="Send to KOReader is not enabled")

    items = (
        db.query(SendQueueItem)
        .options(
            joinedload(SendQueueItem.book).joinedload(Book.files),
            joinedload(SendQueueItem.book).joinedload(Book.book_type),
        )
        .filter(SendQueueItem.user_id == user.id, SendQueueItem.delivered_at.is_(None))
        .order_by(SendQueueItem.created_at.asc())
        .all()
    )

    out = []
    for it in items:
        book = it.book
        if not book or book.status != "active":
            continue
        out.append({
            "id": it.id,
            "book_id": book.id,
            "title": book.title,
            "series": book.series,
            "series_index": book.series_index,
            "author": book.author,
            "book_type": book.book_type.slug if book.book_type else "book",
            "pinned_file_id": it.file_id,
            "files": [
                {"id": f.id, "format": f.format, "file_size": f.file_size}
                for f in book.files
            ],
        })
    return {"count": len(out), "items": out}


@router.post("/tome-sync/inbox/{item_id}/delivered")
def mark_inbox_delivered(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Mark a queued item delivered once the plugin has pulled it. Idempotent."""
    item = (
        db.query(SendQueueItem)
        .filter(SendQueueItem.id == item_id, SendQueueItem.user_id == user.id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    if item.delivered_at is None:
        item.delivered_at = datetime.utcnow()
        db.commit()
    return {"ok": True}


@router.get("/tome-sync/download/{book_id}/{file_id}")
def download_book_via_api_key(
    book_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Stream a book file using API key auth (for the plugin)."""
    book_file = (
        db.query(BookFile)
        .filter(BookFile.id == file_id, BookFile.book_id == book_id)
        .first()
    )
    if not book_file:
        raise HTTPException(status_code=404, detail="File not found")

    if not user_can_see_book(db, user, book_file.book):
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(book_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    from backend.services.metadata_embed import get_baked_path
    from backend.services.ko_hash import record_served_artifact
    serve_path = get_baked_path(book_file.book, book_file)
    record_served_artifact(db, book_file.book_id, book_file, serve_path)

    filename = f"{book_file.book.title}.{book_file.format}"
    return FileResponse(
        str(serve_path),
        media_type="application/octet-stream",
        filename=filename,
    )


# ── API key management (JWT-authed, for the web UI) ───────────────────────────

@router.get("/plugin/api-keys")
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keys = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).all()
    return [
        {
            "id": k.id,
            "label": k.label,
            "key_preview": (k.key_prefix or "tk_") + "…",
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


class CreateKeyRequest(PydanticBaseModel):
    label: str = "KOReader Plugin"


@router.post("/plugin/api-keys", status_code=201)
def create_api_key(
    body: CreateKeyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    key_value = ApiKey.generate()
    api_key = ApiKey(
        user_id=current_user.id,
        key_hash=ApiKey.hash_key(key_value),
        key_prefix=key_value[:11],
        label=body.label,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    audit(db, "plugin_key.created", user_id=current_user.id, username=current_user.username,
          resource_type="plugin_key", resource_id=api_key.id, resource_title=api_key.label,
          details={"prefix": api_key.key_prefix})
    # Return the full key only once — it cannot be retrieved again
    return {
        "id": api_key.id,
        "label": api_key.label,
        "key": key_value,
        "created_at": api_key.created_at.isoformat(),
    }


@router.delete("/plugin/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_key = db.query(ApiKey).filter(
        ApiKey.id == key_id, ApiKey.user_id == current_user.id
    ).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    label = api_key.label
    db.delete(api_key)
    db.commit()
    audit(db, "plugin_key.revoked", user_id=current_user.id, username=current_user.username,
          resource_type="plugin_key", resource_id=key_id, resource_title=label)


# ── Plugin version ────────────────────────────────────────────────────────────

@router.get("/plugin/version")
def plugin_version() -> dict:
    # `version` stays a build-int-as-string for back-compat (existing plugins +
    # web UI read it). `build` (int) is what the self-updater compares; `semver`
    # is display-only.
    return {
        "version": TOMESYNC_PLUGIN_VERSION,
        "build": TOMESYNC_PLUGIN_BUILD,
        "semver": TOMESYNC_PLUGIN_SEMVER,
    }


# ── Plugin download ───────────────────────────────────────────────────────────

def _baked_server_url(request: Request, explicit: str | None) -> str:
    """Resolve the origin baked into the plugin's SERVER_URL.

    Priority:
      1. an explicit ``?server_url=`` (the web UI passes this to dodge the Vite
         dev proxy);
      2. ``TOME_PUBLIC_URL`` config — the authoritative public origin;
      3. the request origin, but with the scheme taken from ``X-Forwarded-Proto``
         when a proxy sent it.

    (3) is the fix for HTTPS-behind-a-proxy deployments: a TLS-terminating proxy
    makes the app server see ``http``, so ``request.base_url`` would bake an
    ``http://`` URL; if the proxy then redirects HTTP→HTTPS, KOReader can't
    follow the 307 on POST/PUT and every session/position sync fails. Honouring
    the forwarded scheme bakes ``https`` instead. When the header is absent
    (plain HTTP / LAN / localhost) the scheme is left untouched, so those
    deployments bake exactly what they did before.

    Shared with the OIDC redirect URI via ``backend.core.urls.public_base_url``.
    """
    return public_base_url(request, explicit)


@router.get("/plugin/koreader")
def download_plugin(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    server_url: str | None = None,
):
    """Generate and download a pre-configured tomesync.koplugin ZIP."""
    # Always mint a fresh key for this download. Plaintext is never stored
    # (only its sha256 hash), so we can't recover a previously-issued plaintext.
    # Existing installs keep working — they have their own plaintext that still
    # hashes to a row in api_keys. Users can revoke unused rows in Settings.
    api_key_value = ApiKey.generate()
    db.add(ApiKey(
        user_id=current_user.id,
        key_hash=ApiKey.hash_key(api_key_value),
        key_prefix=api_key_value[:11],
        label="KOReader Plugin",
    ))
    db.commit()

    server_url = _baked_server_url(request, server_url)

    # Build the ZIP in memory — shim + impl split for self-update:
    #   main.lua       frozen stable shim (no config; runs the rollback machine)
    #   main_impl.lua  the real plugin + baked config; the only file updates replace
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tomesync.koplugin/_meta.lua", _meta_lua())
        zf.writestr("tomesync.koplugin/main.lua", _main_shim_lua())
        zf.writestr("tomesync.koplugin/main_impl.lua",
                    _main_impl_lua(server_url, api_key_value, current_user.username))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=tomesync.koplugin.zip"},
    )


# ── Plugin self-update (impl only) ────────────────────────────────────────────

@router.get("/plugin/main-impl.lua")
def download_main_impl(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_api_key_user),
    server_url: str | None = None,
):
    """Serve the current main_impl.lua for self-update, with the caller's config
    baked in. Authenticated by the plugin's own API key, so config (server URL,
    key, username) survives every update. The shim (main.lua) is frozen and never
    served here."""
    # Reuse the API key the plugin authenticated with, so the refreshed impl keeps
    # the same baked credentials. Recover the plaintext from the request header.
    auth = request.headers.get("authorization", "")
    api_key_value = auth.removeprefix("Bearer ").strip()

    server_url = _baked_server_url(request, server_url)

    return StreamingResponse(
        io.BytesIO(_main_impl_lua(server_url, api_key_value, current_user.username).encode()),
        media_type="text/plain; charset=utf-8",
    )


# ── Lua plugin source ─────────────────────────────────────────────────────────

def _meta_lua() -> str:
    return '''\
local _ = require("gettext")

return {
    name = "tomesync",
    fullname = _("TomeSync"),
    description = _([[Sync reading progress with your Tome library server.
Tracks reading sessions and syncs position across devices.]]),
}
'''


def _main_shim_lua() -> str:
    # Frozen stable shim. Deployed once, never replaced by self-update. No config,
    # no network. Its only jobs: find its own dir, run the anti-brick rollback
    # state machine, dofile main_impl.lua, and return the plugin class (or a valid
    # inert stub if even the backup can't load). Keep this minimal so it never
    # needs to change — if it ever must, that's a manual redeploy.
    return r'''--[[
TomeSync KOReader Plugin — stable shim (frozen; do not edit on-device).
Loads main_impl.lua with same-boot + next-boot rollback so a bad self-update
can never leave TomeSync unloadable.
]]

local logger = require("logger")
logger.info("TomeSync: shim loading...")

local function selfDir()
    local source = debug.getinfo(1, "S").source
    return source:match("^@(.*)/[^/]+$") or "."
end

local DIR      = selfDir()
local IMPL     = DIR .. "/main_impl.lua"
local IMPL_BAK = DIR .. "/main_impl.lua.bak"

local function readFile(path)
    local f = io.open(path, "rb"); if not f then return nil end
    local d = f:read("*a"); f:close(); return d
end

local function writeFile(path, data)
    local f = io.open(path, "wb"); if not f then return false end
    f:write(data); f:close(); return true
end

local function restoreBackup()
    local bak = readFile(IMPL_BAK)
    if not bak then return false end
    return writeFile(IMPL, bak)
end

local function getState()
    local ok, s = pcall(function() return G_reader_settings:readSetting("tomesync_update") end)
    return ok and s or nil
end

local function setState(s)
    pcall(function()
        G_reader_settings:saveSetting("tomesync_update", s)
        G_reader_settings:flush()
    end)
end

local function notify(text)
    pcall(function()
        local InfoMessage = require("ui/widget/infomessage")
        local UIManager   = require("ui/uimanager")
        UIManager:show(InfoMessage:new{ text = text, timeout = 5 })
    end)
end

local function stubPlugin()
    local WidgetContainer = require("ui/widget/container/widgetcontainer")
    local Stub = WidgetContainer:extend{ name = "tomesync", is_doc_only = false }
    function Stub:init() pcall(function() self.ui.menu:registerToMainMenu(self) end) end
    function Stub:addToMainMenu(menu_items)
        menu_items.tomesync = {
            text = "TomeSync (failed to load)",
            callback = function() notify("TomeSync failed to load and could not roll back.\nPlease reinstall the plugin.") end,
        }
    end
    return Stub
end

-- ── Next-boot rollback: an unconfirmed build that never confirmed crashed at init ──
local state = getState()
if state and not state.confirmed then
    state.boots = (state.boots or 0) + 1
    if state.boots >= 2 then
        if restoreBackup() then
            logger.warn("TomeSync: build", state.build, "never confirmed — rolling back")
            setState({ build = state.prev_build, confirmed = true })
            notify("TomeSync update failed — rolled back to previous version.")
        else
            setState(state)  -- no backup to restore; keep the bumped count
        end
    else
        setState(state)
    end
end

-- ── Load impl; same-boot rollback on a load/syntax failure ────────────────────
local ok, plugin = pcall(dofile, IMPL)
if not ok then
    logger.warn("TomeSync: impl failed to load:", tostring(plugin))
    local cur = getState()
    if restoreBackup() then
        setState({ build = (cur and cur.prev_build) or 0, confirmed = true })
        notify("TomeSync update failed — rolled back to previous version.")
        ok, plugin = pcall(dofile, IMPL)
    end
end

if not ok or type(plugin) ~= "table" then
    logger.warn("TomeSync: returning inert stub plugin")
    local sok, stub = pcall(stubPlugin)
    return sok and stub or nil
end

logger.info("TomeSync: shim loaded impl successfully")
return plugin
'''


def _main_impl_lua(server_url: str, api_key: str, username: str) -> str:
    return f'''--[[
TomeSync KOReader Plugin — implementation (replaced in place by self-update).
Syncs reading progress and sessions with a Tome library server.
Browse and download series. Tracks reading sessions and syncs position across devices.

Loaded by the frozen shim (main.lua). Contains the baked config; the shim does not.
]]

local logger = require("logger")
logger.info("TomeSync: main_impl.lua loading...")

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local InfoMessage      = require("ui/widget/infomessage")
local Notification     = require("ui/widget/notification")
local UIManager        = require("ui/uimanager")
local Device           = require("device")
local NetworkMgr       = require("ui/network/manager")
local http             = require("socket.http")
local ltn12            = require("ltn12")
local rapidjson        = require("rapidjson")
local lfs              = require("libs/libkoreader-lfs")
local util             = require("util")
local Menu             = require("ui/widget/menu")
local InputDialog      = require("ui/widget/inputdialog")
local ConfirmBox       = require("ui/widget/confirmbox")
local socketutil       = require("socketutil")
local Dispatcher       = require("dispatcher")
local Event            = require("ui/event")
local LuaSettings      = require("luasettings")
local DataStorage      = require("datastorage")
local ButtonDialog     = require("ui/widget/buttondialog")

-- ── Register in wrench menu (tools tab, after calibre) ──────────────────────
-- Runs once per KOReader process via require() caching.
do
    local reader_order = require("ui/elements/reader_menu_order")
    local fm_order = require("ui/elements/filemanager_menu_order")
    local function insert_after(order_table, section, after_item, new_item)
        local list = order_table[section]
        if not list then return end
        for _, v in ipairs(list) do
            if v == new_item then return end  -- already present
        end
        for i, v in ipairs(list) do
            if v == after_item then
                table.insert(list, i + 1, new_item)
                return
            end
        end
        table.insert(list, new_item)  -- fallback: append
    end
    insert_after(reader_order, "tools", "calibre", "tomesync")
    insert_after(fm_order, "tools", "calibre", "tomesync")
end

-- ── Config (baked in at download time) ───────────────────────────────────────

local SERVER_URL = "{server_url}"
local API_KEY    = "{api_key}"
local USERNAME   = "{username}"

-- Short timeout so unreachable server doesn't freeze the UI

-- Track consecutive failures for backoff. Time-based so it self-heals:
-- once we hit the threshold we go quiet for BACKOFF_COOLDOWN seconds, then
-- let a single probe through. A success clears the latch; a failure re-arms
-- it. Without the time window this was a one-way latch — three failures while
-- the device slept (no WiFi) wedged it permanently until a KOReader restart,
-- because the only reset path (a successful request) was gated behind the latch.
local consecutive_failures = 0
local MAX_BACKOFF_FAILURES = 3
local BACKOFF_COOLDOWN     = 60   -- seconds to stay quiet before re-probing
local backoff_until        = 0    -- os.time() before which requests are skipped

-- Chunk-local (shared across plugin instances): dedupes _initSession when the
-- ReaderReady event reaches more than one live TomeSync instance for the same
-- open. Cleared in onCloseDocument so an immediate reopen still inits.
local last_session_init = {{ book_id = nil, at = 0 }}
-- Once-per-process guard for the state prune (init runs per reader instance).
local state_pruned = false
-- Library-sweep latch: one sweep at a time per KOReader process.
local sweep_running = false
local SWEEP_EXTS = {{ epub = true, pdf = true, cbz = true, cbr = true, mobi = true, azw3 = true }}

-- ── HTTP client ──────────────────────────────────────────────────────────────

local HEARTBEAT_PAGES = 50
-- Longest credible gap between page turns. Anything beyond it is idle time
-- (device left awake, reader asleep) and is not booked to the session. The
-- user can change or disable this in settings; 0 = off.
local IDLE_CAP_DEFAULT_MINUTES = 10
local PLUGIN_VERSION  = "{TOMESYNC_PLUGIN_VERSION}"
local BUILD           = {TOMESYNC_PLUGIN_BUILD}      -- monotonic; the only thing compared
local SEMVER          = "{TOMESYNC_PLUGIN_SEMVER}"   -- human-facing display only

local function urlEncode(s)
    return s:gsub("([^%w%-%.%_%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
end

-- rapidjson decodes JSON null to a sentinel (rapidjson.null), not Lua nil.
-- Normalize it so "no rating" compares equal to an absent baseline value.
local function jval(v)
    if v == nil or v == rapidjson.null then return nil end
    return v
end

-- Device identity as reported to Tome: the label on live sessions and imported
-- history, and the key of this device's reading-history sync watermark. A
-- user-set name (Settings > Device name) wins; otherwise KOReader's
-- Device.model ("KindlePaperWhite4", "Kobo_clara", an Android product name);
-- "KOReader" only when neither exists. Builds before 40 always reported the
-- literal "KOReader" (they called a Device method that does not exist in
-- KOReader), so every device of a user shared ONE history watermark - see
-- _adoptDeviceName for the one-time migration.
local LEGACY_DEVICE_NAME = "KOReader"
local function autoDeviceName()
    local model = Device and Device.model
    if type(model) == "string" and model ~= "" then return model end
    return LEGACY_DEVICE_NAME
end
local function deviceName()
    local custom = G_reader_settings:readSetting("tomesync_device_name")
    if type(custom) == "string" and custom ~= "" then return custom end
    return autoDeviceName()
end

local function idleCapSeconds()
    local mins = G_reader_settings:readSetting("tomesync_idle_cap_minutes")
    if mins == nil then mins = IDLE_CAP_DEFAULT_MINUTES end
    if mins == 0 then return math.huge end
    return mins * 60
end

local function apiRequest(method, path, body)
    -- Skip immediately if WiFi is not connected — zero blocking
    if not NetworkMgr:isConnected() then
        return nil, "offline"
    end

    -- Skip requests while the backoff window is open. Once it expires we fall
    -- through and let one probe attempt the request, so connectivity recovery
    -- is detected automatically rather than only on a KOReader restart.
    if consecutive_failures >= MAX_BACKOFF_FAILURES and os.time() < backoff_until then
        logger.warn("TomeSync: skipping request (server unreachable, backing off)")
        return nil, "backoff"
    end

    local url = SERVER_URL .. "/api" .. path
    local req_body = body and rapidjson.encode(body) or nil
    local resp_chunks = {{}}

    local headers = {{
        ["Authorization"] = "Bearer " .. API_KEY,
        ["Content-Type"]  = "application/json",
        ["Accept"]        = "application/json",
    }}
    if req_body then
        headers["Content-Length"] = tostring(#req_body)
    end

    -- Bounded per-request timeouts (block, total) via socketutil — no global
    -- http.TIMEOUT mutation. Block 5s catches a dead route fast; the total is
    -- deliberately generous: annotation-sync responses can be large and slow
    -- device wifi through an HTTPS proxy must not get truncated mid-body.
    socketutil:set_timeout(5, 45)
    local ok, code = http.request({{
        url     = url,
        method  = method,
        headers = headers,
        source  = req_body and ltn12.source.string(req_body) or nil,
        sink    = ltn12.sink.table(resp_chunks),
    }})
    socketutil:reset_timeout()

    if not ok then
        consecutive_failures = consecutive_failures + 1
        backoff_until = os.time() + BACKOFF_COOLDOWN
        logger.warn("TomeSync: request failed:", tostring(code),
                     "(" .. consecutive_failures .. "/" .. MAX_BACKOFF_FAILURES .. ")")
        return nil, code
    end

    -- Server reachable — clear the backoff latch
    consecutive_failures = 0
    backoff_until = 0

    local resp_body = table.concat(resp_chunks)
    if code == 404 then return nil, 404 end
    if code >= 200 and code < 300 then
        local ok2, parsed = pcall(rapidjson.decode, resp_body)
        if ok2 then return parsed, code end
        return {{}}, code
    end

    logger.warn("TomeSync: HTTP", code, resp_body)
    return nil, code
end

-- ── Format preference & download helpers ────────────────────────────────────

local FORMAT_PREFERENCE = {{"epub", "kepub.epub", "cbz", "pdf", "mobi", "azw3"}}

local function pickBestFile(files)
    if not files or #files == 0 then return nil end
    for _, fmt in ipairs(FORMAT_PREFERENCE) do
        for _, f in ipairs(files) do
            if f.format == fmt then return f end
        end
    end
    return files[1]
end

local function downloadFile(book_id, file_id, dest_path, total_size, progress_cb)
    if not NetworkMgr:isConnected() then
        return false, "offline"
    end

    local url = SERVER_URL .. "/api/tome-sync/download/" .. book_id .. "/" .. file_id
    local fh = io.open(dest_path, "wb")
    if not fh then
        return false, "cannot open file for writing"
    end

    -- Generous total budget for large files; the block timeout still catches
    -- a stalled connection quickly (no global http.TIMEOUT mutation).
    socketutil:set_timeout(15, 900)

    -- Count bytes as they stream to disk; repaint at most every 5% (known
    -- size) or 256 KB (unknown) so e-ink isn't flooded — a 300 MB CBZ used
    -- to sit mute for minutes with no sign of life.
    local sink = ltn12.sink.file(fh)
    if progress_cb then
        local base_sink, received, last_bucket = sink, 0, 0
        sink = function(chunk, err)
            if chunk then
                received = received + #chunk
                local bucket
                if type(total_size) == "number" and total_size > 0 then
                    bucket = math.floor(received * 20 / total_size)
                else
                    bucket = math.floor(received / 262144)
                end
                if bucket ~= last_bucket then
                    last_bucket = bucket
                    pcall(progress_cb, received, total_size)
                end
            end
            return base_sink(chunk, err)
        end
    end

    local ok, code = http.request({{
        url     = url,
        method  = "GET",
        headers = {{
            ["Authorization"] = "Bearer " .. API_KEY,
        }},
        sink = sink,
    }})

    socketutil:reset_timeout()

    if not ok or (type(code) == "number" and code >= 300) then
        os.remove(dest_path)
        return false, tostring(code or "request failed")
    end

    return true
end

-- ── Download path templates ──────────────────────────────────────────────────

-- mkdir -p: create every missing directory level of an absolute path.
local function mkdirp(path)
    local acc = path:sub(1, 1) == "/" and "/" or ""
    for seg in path:gmatch("[^/]+") do
        acc = acc .. seg
        lfs.mkdir(acc)
        acc = acc .. "/"
    end
end

-- TOMESYNC_TEMPLATE_BEGIN (kept dependency-free except util — extracted and
-- unit-tested standalone by the backend test suite)
-- Render a download path template into a relative path (no extension).
-- Tokens: {{book_type}} {{series}} {{title}} {{author}} {{volume}} {{volume:00}}
-- plus {{Lower(token)}} / {{Upper(token)}}. "/" separates folders. Empty tokens
-- are dropped along with orphaned ASCII separators around them, so one
-- template works for series books and standalones alike. Every segment is
-- sanitized, so a template can never escape the base directory. Returns nil
-- when a token is unknown or the whole template renders to nothing usable —
-- callers fall back to the built-in layout.
local function renderDownloadPath(template, ctx)
    local function fmtVolume(spec)
        local vol = ctx.volume
        if type(vol) ~= "number" then return "" end
        if vol == math.floor(vol) then
            local pad = spec and spec:match("^:(0+)$")
            if pad then return string.format("%0" .. #pad .. "d", vol) end
            return tostring(math.floor(vol))
        end
        return tostring(vol)
    end
    local function tokenValue(name)
        local case
        local inner = name:match("^Lower%((.+)%)$")
        if inner then case, name = "lower", inner end
        if not case then
            inner = name:match("^Upper%((.+)%)$")
            if inner then case, name = "upper", inner end
        end
        local val
        if name == "volume" or name:match("^volume:") then
            val = fmtVolume(name:match("^volume(:.*)$"))
        elseif name == "book_type" then val = ctx.book_type or ""
        elseif name == "series" then val = ctx.series or ""
        elseif name == "title" then val = ctx.title or ""
        elseif name == "author" then val = ctx.author or ""
        else return nil end
        if case == "lower" then val = val:lower()
        elseif case == "upper" then val = val:upper() end
        -- A "/" inside a value must not create folders (only template
        -- slashes separate segments).
        return (val:gsub("/", "-"))
    end
    local unknown = false
    local rendered = template:gsub("%{{(.-)%}}", function(name)
        local v = tokenValue(name)
        if v == nil then unknown = true; return "" end
        return v
    end)
    if unknown then return nil end
    local segments = {{}}
    for seg in rendered:gmatch("[^/]+") do
        seg = seg:gsub("%s+", " ")
        -- Collapse separator runs left behind by empty tokens ("a - - b"),
        -- then strip orphaned separators at the ends. ASCII-only on purpose:
        -- multibyte chars in Lua pattern classes match stray bytes.
        local prev
        repeat
            prev = seg
            seg = seg:gsub("([%-_,])%s*[%-_,]", "%1")
        until seg == prev
        seg = seg:gsub("^[%s%-_,.]+", ""):gsub("[%s%-_,.]+$", "")
        seg = util.getSafeFilename(seg)
        if seg ~= "" and seg ~= "." and seg ~= ".." then
            table.insert(segments, seg)
        end
    end
    if #segments == 0 then return nil end
    return table.concat(segments, "/")
end
-- TOMESYNC_TEMPLATE_END

-- Preset for "Flat in home folder": no folders, series + volume in the
-- filename so flat layouts cannot collide across series. Separator-only
-- between tokens (no glued literals like "Vol."), so empty tokens collapse
-- cleanly for standalones. ASCII separators only (see the cleanup note in
-- renderDownloadPath).
local FLAT_TEMPLATE = "{{series}} - {{volume:00}} - {{title}}"

-- ── Connectivity (issue #38) ─────────────────────────────────────────────────
-- Interactive entry points route through this instead of hitting the network
-- directly. Default: run the action as-is, so the existing isConnected guards
-- behave exactly as before. With "Auto-connect WiFi" enabled, KOReader brings
-- the connection back up first (per the user's wifi_enable_action setting) and
-- runs the action once connected — devices like PocketBook aggressively sleep
-- the radio, which otherwise leaves every action failing with "offline".
local function whenConnected(fn)
    if G_reader_settings:isTrue("tomesync_auto_connect")
            and not NetworkMgr:isConnected() then
        NetworkMgr:runWhenConnected(fn)
        return
    end
    fn()
end

-- ── Self-update helpers ──────────────────────────────────────────────────────

local function implDir()
    local source = debug.getinfo(1, "S").source
    return source:match("^@(.*)/[^/]+$") or "."
end

local IMPL_PATH = implDir() .. "/main_impl.lua"
local IMPL_BAK  = IMPL_PATH .. ".bak"

local function readWhole(path)
    local f = io.open(path, "rb"); if not f then return nil end
    local d = f:read("*a"); f:close(); return d
end

local function writeWhole(path, data)
    local f = io.open(path, "wb"); if not f then return false end
    f:write(data); f:close(); return true
end

-- Raw (non-JSON) authenticated GET, used to fetch the new impl text.
local function fetchText(path)
    if not NetworkMgr:isConnected() then return nil, "offline" end
    local chunks = {{}}
    socketutil:set_timeout(10, 60)
    local ok, code = http.request({{
        url     = SERVER_URL .. "/api" .. path,
        method  = "GET",
        headers = {{ ["Authorization"] = "Bearer " .. API_KEY }},
        sink    = ltn12.sink.table(chunks),
    }})
    socketutil:reset_timeout()
    if not ok then return nil, code end
    if type(code) == "number" and code >= 300 then return nil, code end
    return table.concat(chunks), code
end

-- Reject anything that isn't a plausible, compilable impl before swapping it in.
local function validateImpl(body)
    if not body or #body < 15000 then return false, "too small" end
    if not load(body) then return false, "does not compile" end
    if not body:find("function TomeSync:init", 1, true) then return false, "missing init" end
    if not body:find("return TomeSync", 1, true) then return false, "missing return" end
    return true
end

-- ── Plugin widget ────────────────────────────────────────────────────────────

local TomeSync = WidgetContainer:extend{{
    name        = "tomesync",
    is_doc_only = false,
}}

function TomeSync:init()
    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = nil
    self.progress_start = nil
    self.last_progress  = nil
    self.enabled        = true
    -- Dedicated state file: the plugin's data tables live in their own
    -- LuaSettings file, NOT in G_reader_settings — KOReader parses the global
    -- settings file at every boot, and these tables grow with the library.
    -- (tomesync_update stays global: the frozen shim reads it and is never
    -- replaced by self-update.)
    self.state = LuaSettings:open(DataStorage:getSettingsDir() .. "/tomesync_state.lua")
    self:_migrateState()
    self.book_map       = self.state:readSetting("tomesync_book_map") or {{}}
    self.pending_sessions = self.state:readSetting("tomesync_pending_sessions") or {{}}
    -- Send-to-KOReader inbox (beta): enabled only if the server reports the
    -- feature; count drives the menu badge. Populated by the launch poll below.
    self.inbox_enabled  = false
    self.inbox_count    = 0
    self.inbox_items    = {{}}
    -- Web-adoption ledger: real_anchor -> provisional "web:" anchor, persisted so a
    -- failed push retries next sync (baseline alone would swallow the adoption).
    self.adopt_pending = self.state:readSetting("tomesync_adopt_pending") or {{}}
    -- "<book_id>|<local pos0>" -> {{ anchor, anchor_end }} of the SERVER
    -- identity for foreign highlights that had to be re-anchored on this copy
    -- (see _applyForeign). Keys are book-scoped: xPointers are only unique
    -- WITHIN a book, and structurally common positions (p[1]/text().0) would
    -- otherwise collide across books and mistranslate pushes. Persisted so
    -- repairs survive restarts.
    self.repair_map = self.state:readSetting("tomesync_repair_map") or {{}}
    self._heartbeat_armed = false
    self._heartbeat_task = function() self:_heartbeatNow() end
    -- Per-book annotation sync baseline: book_id -> {{ anchor -> mtime }} as of last
    -- sync. Lets a diff tell "I deleted this" from "this is new from another device".
    self.annot_baseline = self.state:readSetting("tomesync_annot_baseline") or {{}}
    -- Per-book rating sync baseline: book_id (string) -> {{ rating=, review= }} as of
    -- the last reconcile. Lets a diff tell which side (device or Tome) changed.
    self.rating_baseline = self.state:readSetting("tomesync_rating_baseline") or {{}}
    -- Ratings set offline (or lost to a server error) that never reached Tome.
    -- Keyed by book_id (string) so re-rating the same book before a flush keeps
    -- only the latest value. Flushed on resume / Sync now / close like sessions:
    -- the per-book open/close push alone misses a book you rate and never reopen
    -- (e.g. a finished book), so the rating would otherwise sit unsent forever.
    self.pending_ratings = self.state:readSetting("tomesync_pending_ratings") or {{}}
    self:_pruneState()
    self:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)
    logger.info("TomeSync: init complete, menu registered,",
                #self.pending_sessions, "pending sessions")

    -- Anti-brick confirm (§3): init() reached the end, so this build is good.
    -- Mark it confirmed now (and flush) so the shim never rolls it back.
    local ustate = G_reader_settings:readSetting("tomesync_update")
    if ustate and ustate.build == BUILD and not ustate.confirmed then
        ustate.confirmed = true
        G_reader_settings:saveSetting("tomesync_update", ustate)
        G_reader_settings:flush()
        logger.info("TomeSync: confirmed build", BUILD)
    end

    -- Opt-in: a deferred, non-blocking update check shortly after startup.
    if G_reader_settings:isTrue("tomesync_auto_check") then
        UIManager:scheduleIn(8, function()
            self:checkForUpdate(function(avail)
                if avail then self:_promptUpdate(avail) end
            end)
        end)
    end

    -- Send-to-KOReader inbox: a deferred, non-blocking poll so the menu can show
    -- an "Inbox (N)" badge. Guarded (offline = no-op; 404 = feature off → no
    -- badge), so it is safe to run on every launch regardless of server support.
    UIManager:scheduleIn(8, function() pcall(function() self:_refreshInbox() end) end)

    -- Reading-history backfill: opt-in, deferred, non-blocking. First run pushes
    -- the entire KOReader history (chunked + resumable); later runs only new rows.
    if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
        UIManager:scheduleIn(12, function() pcall(function() self:_syncReadingStats(false) end) end)
    end
end

-- The data tables that live in the dedicated state file (tomesync_update and
-- the boolean preferences stay in G_reader_settings — the frozen shim reads
-- the former, and the latter are what user-settings files are for).
local STATE_KEYS = {{
    "tomesync_book_map", "tomesync_pending_sessions", "tomesync_adopt_pending",
    "tomesync_repair_map", "tomesync_annot_baseline", "tomesync_rating_baseline",
    "tomesync_pending_ratings",
}}

function TomeSync:_saveState(key, value)
    self.state:saveSetting(key, value)
    -- G_reader_settings flushed on app close; our file must flush itself. Save
    -- sites are already at meaningful boundaries (sync done, queue changed), so
    -- write-through is the right durability trade for a file this small.
    self.state:flush()
end

function TomeSync:_migrateState()
    -- One-time move of the data tables out of G_reader_settings. Crash-safe
    -- order: write + flush the new file FIRST, delete the old keys after — a
    -- crash in between leaves a harmless duplicate, and the marker branch
    -- below re-deletes leftovers on the next boot.
    if self.state:readSetting("migrated_from_global") then
        local leftover = false
        for _, k in ipairs(STATE_KEYS) do
            if G_reader_settings:has(k) then
                G_reader_settings:delSetting(k)
                leftover = true
            end
        end
        if leftover then G_reader_settings:flush() end
        return
    end
    local found = false
    for _, k in ipairs(STATE_KEYS) do
        local v = G_reader_settings:readSetting(k)
        if v ~= nil then
            self.state:saveSetting(k, v)
            found = true
        end
    end
    self.state:saveSetting("migrated_from_global", true)
    self.state:flush()
    for _, k in ipairs(STATE_KEYS) do G_reader_settings:delSetting(k) end
    G_reader_settings:flush()
    if found then
        logger.info("TomeSync: migrated plugin state to tomesync_state.lua")
    end
end

function TomeSync:_pruneState()
    -- Drop per-book state whose file is gone so the state file can't grow
    -- unboundedly. Queues (pending_sessions/pending_ratings) are never pruned —
    -- they are owed to the server regardless of the local file's fate. Baseline
    -- loss is delete-safe by construction: deletes are only ever pushed FROM
    -- baseline entries, so a wrongly-pruned baseline can only cause a harmless
    -- re-upsert echo, never a delete.
    if state_pruned then return end
    state_pruned = true
    local changed = false
    local ids = {{}}
    for path, id in pairs(self.book_map) do
        if lfs.attributes(path, "mode") == "file" then
            ids[tostring(id)] = true
        else
            self.book_map[path] = nil
            changed = true
        end
    end
    local function pruneById(tbl)
        for key in pairs(tbl) do
            -- repair_map keys are "<book_id>|<anchor>"; baselines use "<book_id>"
            local id = key:match("^(%d+)|") or key
            if not ids[id] then
                tbl[key] = nil
                changed = true
            end
        end
    end
    pruneById(self.annot_baseline)
    pruneById(self.rating_baseline)
    pruneById(self.repair_map)
    -- Sweep ledger is keyed by file path directly.
    local sweep = self.state:readSetting("tomesync_sweep")
    if sweep and sweep.done then
        for path in pairs(sweep.done) do
            if lfs.attributes(path, "mode") ~= "file" then
                sweep.done[path] = nil
                changed = true
            end
        end
        if changed then self.state:saveSetting("tomesync_sweep", sweep) end
    end
    if changed then
        self.state:saveSetting("tomesync_book_map", self.book_map)
        self.state:saveSetting("tomesync_annot_baseline", self.annot_baseline)
        self.state:saveSetting("tomesync_rating_baseline", self.rating_baseline)
        self.state:saveSetting("tomesync_repair_map", self.repair_map)
        self.state:flush()
        logger.info("TomeSync: pruned state for books no longer on disk")
    end
end

function TomeSync:onDispatcherRegisterActions()
    Dispatcher:registerAction("tome_open_menu", {{
        category = "none",
        event    = "TomeOpenMenu",
        title    = "TomeSync: Open menu",
        general  = true,
    }})
    Dispatcher:registerAction("tome_browse_series", {{
        category = "none",
        event    = "TomeBrowseSeries",
        title    = "TomeSync: Browse series",
        general  = true,
    }})
    Dispatcher:registerAction("tome_sync_annotations", {{
        category = "none",
        event    = "TomeSyncAnnotations",
        title    = "TomeSync: Sync highlights",
        reader   = true,
    }})
    Dispatcher:registerAction("tome_sync_stats", {{
        category = "none",
        event    = "TomeSyncStats",
        title    = "TomeSync: Sync reading history",
        general  = true,
    }})
end

function TomeSync:onTomeOpenMenu()
    self:_openMenu()
    return true
end

function TomeSync:onTomeBrowseSeries()
    self:_browseSeriesMenu()
    return true
end

function TomeSync:onTomeSyncAnnotations()
    if not self.book_id then
        UIManager:show(InfoMessage:new{{ text = "No book resolved. Open a book first.", timeout = 3 }})
        return true
    end
    whenConnected(function()
        local resp = self:_syncAnnotations()
        if resp == nil and not NetworkMgr:isConnected() then
            UIManager:show(InfoMessage:new{{ text = "Offline — highlights will sync later.", timeout = 3 }})
        else
            local n = (resp and resp.annotations) and #resp.annotations or 0
            UIManager:show(InfoMessage:new{{ text = "Highlights synced (" .. n .. " on this book).", timeout = 3 }})
        end
    end)
    return true
end

function TomeSync:onTomeSyncStats()
    whenConnected(function() self:_syncReadingStats(true) end)
    return true
end

function TomeSync:onReaderReady()
    if not self.enabled then return end
    local doc = self.ui and self.ui.document
    if not doc then return end

    self.book_id = self.book_map[doc.file]

    -- If no cached mapping, try to resolve by filename
    if not self.book_id then
        self:_tryResolve()
    end

    if not self.book_id then return end

    self:_initSession()
    -- Pull highlights from other devices (and push any local changes). This is the
    -- moment a device picks up annotations made elsewhere. Deferred a tick so the
    -- annotation module is fully settled before we merge into it.
    UIManager:scheduleIn(1, function() pcall(function() self:_syncAnnotations() end) end)
    -- Reconcile the book's rating with Tome (pull web rating onto the device, or
    -- push a device rating that never reached the server). Deferred likewise.
    UIManager:scheduleIn(1, function() pcall(function() self:_pullRatingAtOpen() end) end)
end

function TomeSync:onPageUpdate(pageno)
    if not self.enabled then return end
    if pageno == false then return end

    -- Retry resolve if book wasn't matched on open (e.g. WiFi was not ready)
    if not self.book_id then
        self:_tryResolve()
        if self.book_id then
            self:_initSession()
        end
        return
    end

    self:_creditActivity(os.time())
    self.page_count = self.page_count + 1
    -- Idle-debounced heartbeat: reaching the page threshold ARMS the push, and
    -- every further turn re-delays it — the HTTP call runs 10s after the LAST
    -- page turn, never on the page-turn path itself (which used to stall the
    -- turn for up to the request timeout on a flaky network).
    if self.page_count % HEARTBEAT_PAGES == 0 or self._heartbeat_armed then
        self._heartbeat_armed = true
        UIManager:unschedule(self._heartbeat_task)
        UIManager:scheduleIn(10, self._heartbeat_task)
    end
end

-- Credit the time spent on the page just finished, capped at the idle
-- limit — a gap longer than the cap means the device sat awake unread.
function TomeSync:_creditActivity(now)
    if self.last_activity then
        local gap = now - self.last_activity
        if gap > 0 then
            self.active_seconds = (self.active_seconds or 0) + math.min(gap, idleCapSeconds())
        end
    end
    self.last_activity = now
end

-- Active reading time and honest end for the current session. Every gap
-- between page turns is credited at most the idle cap, including the tail
-- after the last turn — a device left awake overnight books the evening's
-- reading, not the night, and the session ends when the reading did. With
-- the cap off this degrades to exactly now - session_start / now.
function TomeSync:_sessionTotals(now)
    if not self.session_start then return 0, now end
    local last = self.last_activity or self.session_start
    local tail = now - last
    if tail > 0 then tail = math.min(tail, idleCapSeconds()) else tail = 0 end
    return (self.active_seconds or 0) + tail, last + tail
end

function TomeSync:_heartbeatNow()
    self._heartbeat_armed = false
    if not self.enabled or not self.book_id then return end
    local pct = self:_getCurrentPercentage()
    self.last_progress = pct
    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress   = self:_getCurrentProgress(),
        percentage = pct,
        device     = deviceName(),
    }})
    -- Flush any offline sessions while we know WiFi is up
    self:_flushPendingSessions()
    self:_flushPendingRatings()
end

function TomeSync:onSuspend()
    UIManager:unschedule(self._heartbeat_task)
    self._heartbeat_armed = false
    if not self.enabled then return end

    if self.book_id then
        -- Record the reading session (lid close = end of session)
        local pct      = self:_getCurrentPercentage()
        local cfi      = self:_getCurrentProgress()
        local duration, session_end = self:_sessionTotals(os.time())
        local dev      = deviceName()

        pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
            progress = cfi, percentage = pct, device = dev,
        }})

        -- Sync highlights/notes alongside position (bidirectional merge with the server).
        pcall(function() self:_syncAnnotations() end)
        -- Push a rating set this session (lid close ends the session like a book close).
        pcall(function() self:_pushRatingOnLeave() end)

        if duration > 10 then
            local session = {{
                book_id          = self.book_id,
                started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
                ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", session_end),
                duration_seconds = duration,
                progress_start   = self.progress_start,
                progress_end     = pct,
                pages_turned     = self.page_count,
                device           = dev,
                session_uuid     = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev),
            }}
            local sok, sresult, scode = pcall(apiRequest, "POST", "/tome-sync/session", session)
            if not sok or not sresult or (type(scode) == "number" and scode >= 300) then
                -- Failed to send — save for later
                table.insert(self.pending_sessions, session)
                -- Cap at 50 to prevent unbounded growth
                while #self.pending_sessions > 50 do
                    table.remove(self.pending_sessions, 1)
                end
                self:_saveState("tomesync_pending_sessions", self.pending_sessions)
                logger.info("TomeSync: session queued for retry, pending:", #self.pending_sessions)
            end
        end
    end

    self:_suspendSync()
end

-- Sync on suspend (issue #128): before the device sleeps, catch up everything
-- still queued — pending sessions/ratings and, when the launch backfill is
-- enabled, the KOReader reading-history import — so stats are current in the
-- morning without waking the device. Two opt-in levels:
--   tomesync_sync_on_suspend   sync only if WiFi is already connected
--   tomesync_suspend_wifi      also bring the radio up first (aggressive)
-- The device may finish suspending before WiFi associates or mid-transfer;
-- every step here is queue-or-resume safe (sessions/ratings re-flush, the
-- history import resumes from the server watermark), so a cut connection just
-- finishes on the next opportunity.
function TomeSync:_suspendSync()
    if not G_reader_settings:isTrue("tomesync_sync_on_suspend") then return end
    local function catchUp()
        self:_flushPendingSessions()
        self:_flushPendingRatings()
        if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
            pcall(function() self:_syncReadingStats(false) end)
        end
    end
    if NetworkMgr:isConnected() then
        catchUp()
    elseif G_reader_settings:isTrue("tomesync_suspend_wifi") then
        -- Deliberately NOT beforeWifiAction/whenConnected: those honour the
        -- user's wifi_enable_action, and "prompt" would pop a dialog onto the
        -- sleep screen. The toggle's promise is "turn WiFi on", so do exactly
        -- that. The system powers the radio back down as part of suspend.
        pcall(function() NetworkMgr:turnOnWifiAndWaitForConnection(catchUp) end)
    end
end

function TomeSync:onResume()
    if not self.enabled or not self.book_id then return end

    -- Start a fresh session (lid open = new session)
    self.session_start  = os.time()
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = self.session_start
    self.progress_start = self:_getCurrentPercentage()
    self.last_progress  = self.progress_start

    -- Push position on wake — catches up after offline periods
    self:_pushPosition()

    -- Flush any pending sessions / ratings from offline periods
    self:_flushPendingSessions()
    self:_flushPendingRatings()
end

-- WiFi just came back: drop the backoff latch immediately so we don't sit out
-- the cooldown, then catch up anything queued while we were offline. The latch
-- is chunk-local, so clearing it here unblocks every TomeSync instance at once.
function TomeSync:onNetworkConnected()
    consecutive_failures = 0
    backoff_until = 0
    -- No book_id guard: pending sessions/ratings and the history import are
    -- device-level, and WiFi often comes up in the file manager (morning
    -- catch-up) before any book is opened.
    if not self.enabled then return end
    self:_flushPendingSessions()
    self:_flushPendingRatings()
    -- Catch the reading-history backfill up too — previously launch-only, so
    -- turning WiFi on without restarting KOReader left stats stale (issue #128).
    -- Deferred so it doesn't contend with whatever brought the network up.
    if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
        UIManager:scheduleIn(4, function()
            pcall(function() self:_syncReadingStats(false) end)
        end)
    end
end

-- ── KOReader statistics.sqlite3 import (reading-history backfill) ────────────
-- Pushes KOReader's own per-page reading log to Tome (time & pages ONLY — never
-- read-status). Chunked + resumable: the server keeps a per-device watermark, so
-- an interrupted run resumes next launch and never re-sends (idempotent server
-- side). First run backfills the whole history; later runs send only new rows.
function TomeSync:_statsDbPath()
    return require("datastorage"):getSettingsDir() .. "/statistics.sqlite3"
end

-- Move this device's imported reading history (page-stat rows + sync
-- watermark) from the name it previously reported to the one it reports now.
-- Live sessions keep the label they were recorded with: on a multi-device
-- setup the old label was shared, so they can't be attributed after the fact.
-- Idempotent server-side; a failed call is simply retried on the next sync.
function TomeSync:_renameDeviceHistory(from, to)
    if not from or from == "" or from == to then return true end
    local ok, resp = pcall(apiRequest, "POST", "/tome-sync/stats/rename-device",
        {{ from_device = from, to_device = to }})
    return ok and type(resp) == "table"
end

-- `tomesync_device_name_adopted` remembers the last name whose history was
-- migrated. First run on build 40+ moves the pre-40 "KOReader" history; a
-- later rename in Settings moves it again from the previous name.
-- Returns true when this device's history is known to be filed under its
-- current name (nothing to do, or the move succeeded). False = the server did
-- not confirm; the caller must NOT sync history from watermark 0 in that state,
-- or sittings the user deleted in Tome would be re-imported.
function TomeSync:_adoptDeviceName()
    local name = deviceName()
    if name == LEGACY_DEVICE_NAME then return true end
    local adopted = G_reader_settings:readSetting("tomesync_device_name_adopted")
    if adopted == name then return true end
    if self:_renameDeviceHistory(adopted or LEGACY_DEVICE_NAME, name) then
        G_reader_settings:saveSetting("tomesync_device_name_adopted", name)
        return true
    end
    return false
end

-- Settings > Device name: the label Tome shows for this device. Empty restores
-- the automatic name (KOReader's device model).
function TomeSync:_editDeviceName()
    local current = G_reader_settings:readSetting("tomesync_device_name") or ""
    local dialog
    dialog = InputDialog:new{{
        title       = "Device name",
        input       = current,
        hint        = autoDeviceName(),
        description = "How this device is labelled in Tome's reading log and "
                      .. "stats. Leave empty to use the automatic name "
                      .. "(\\"" .. autoDeviceName() .. "\\"). Give each "
                      .. "device a distinct name - the reading-history sync "
                      .. "keeps its place per device name.",
        buttons     = {{{{
            {{
                text     = "Cancel",
                id       = "close",
                callback = function() UIManager:close(dialog) end,
            }},
            {{
                text             = "Save",
                is_enter_default = true,
                callback         = function()
                    local name = dialog:getInputText():gsub("^%s+", ""):gsub("%s+$", "")
                    if name == "" then
                        G_reader_settings:delSetting("tomesync_device_name")
                    else
                        G_reader_settings:saveSetting("tomesync_device_name", name)
                    end
                    UIManager:close(dialog)
                    if NetworkMgr:isConnected() then
                        pcall(function() self:_adoptDeviceName() end)
                    end
                    UIManager:show(InfoMessage:new{{
                        text = "Device name: " .. deviceName(), timeout = 2 }})
                end,
            }},
        }}}},
    }}
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function TomeSync:_syncReadingStats(manual)
    local function tell(msg)
        if manual then UIManager:show(InfoMessage:new{{ text = msg, timeout = 3 }}) end
    end
    if self._stats_syncing then tell("Reading-history sync already running."); return end
    if not NetworkMgr:isConnected() then tell("Offline - reading history will sync later."); return end
    local path = self:_statsDbPath()
    if lfs.attributes(path, "mode") ~= "file" then tell("No KOReader statistics database found."); return end

    -- Carry this device's history + watermark over from the name it used to
    -- report, so a name change (incl. the build-40 move off "KOReader") resumes
    -- instead of re-sending the whole database. Until the server confirms the
    -- move, don't sync at all: a run from watermark 0 would re-import sittings
    -- the user deleted in Tome.
    if not self:_adoptDeviceName() then
        tell("Could not register this device's name with Tome - reading-history sync postponed.")
        return
    end

    -- Server is the source of truth for "how far did we get".
    local wm = apiRequest("GET", "/tome-sync/stats/watermark?device=" .. urlEncode(deviceName()))
    local since = 0
    if type(wm) == "table" and tonumber(wm.last_start_time_synced) then
        since = tonumber(wm.last_start_time_synced)
    end

    local SQ3 = require("lua-ljsqlite3/init")

    -- One cheap metadata pass: the book table is one row per book, and COUNT(*)
    -- sizes the progress message. Page rows are windowed below — memory stays
    -- flat no matter how many years of history the device holds (a real Kindle
    -- DB measures tens of thousands of page_stat_data rows; the old slurp-all
    -- approach materialised every one of them as a Lua table at once).
    local books_by_id, total = {{}}, 0
    do
        local opened, conn = pcall(SQ3.open, path, "ro")
        if not opened or not conn then tell("Could not open statistics database."); return end
        local read_ok = pcall(function()
            -- ljsqlite3 returns INTEGER columns as int64 cdata, which rapidjson can't
            -- encode — tonumber() every numeric field. (TEXT comes back as Lua strings.)
            local bstmt = conn:prepare("SELECT id, md5, title, authors, pages, total_read_pages FROM book")
            for r in bstmt:rows() do
                books_by_id[tonumber(r[1])] = {{ ko_id = tonumber(r[1]), md5 = r[2] or "", title = r[3] or "",
                                                 authors = r[4], pages = tonumber(r[5]), total_read_pages = tonumber(r[6]) }}
            end
            bstmt:close()
            local cstmt = conn:prepare("SELECT COUNT(*) FROM page_stat_data WHERE start_time >= " .. since)
            for r in cstmt:rows() do total = tonumber(r[1]) or 0 end
            cstmt:close()
        end)
        pcall(function() conn:close() end)
        if not read_ok then tell("Could not read statistics database."); return end
    end
    if total == 0 then tell("Reading history already up to date."); return end

    self._stats_syncing = true
    local dev   = deviceName()
    local CHUNK = 500
    local sent  = 0
    -- Keyset cursor, strictly after (start_time, rowid). Starting at
    -- (watermark, -1) keeps the old ">= watermark" boundary refetch: rows that
    -- share the watermark second are re-sent and no-op server-side (the import
    -- is INSERT OR IGNORE on the identity key). An interrupted run resumes
    -- from the server watermark on the next launch, exactly as before.
    local cur_start, cur_rowid = since, -1

    local function readWindow()
        local opened, conn = pcall(SQ3.open, path, "ro")
        if not opened or not conn then return nil end
        local rows = {{}}
        local ok = pcall(function()
            local stmt = conn:prepare(string.format(
                "SELECT rowid, id_book, page, start_time, duration, total_pages FROM page_stat_data "
                .. "WHERE start_time > %d OR (start_time = %d AND rowid > %d) "
                .. "ORDER BY start_time, rowid LIMIT %d", cur_start, cur_start, cur_rowid, CHUNK))
            for r in stmt:rows() do
                rows[#rows + 1] = {{ rowid = tonumber(r[1]), ko_id = tonumber(r[2]), page = tonumber(r[3]),
                                     start_time = tonumber(r[4]), duration = tonumber(r[5]), total_pages = tonumber(r[6]) }}
            end
            stmt:close()
        end)
        pcall(function() conn:close() end)
        if not ok then return nil end
        return rows
    end

    local function finish(msg, force)
        self._stats_syncing = false
        if manual or force then UIManager:show(InfoMessage:new{{ text = msg, timeout = 3 }}) end
    end
    local sendNext
    sendNext = function()
        if not NetworkMgr:isConnected() then
            finish("Reading-history sync paused (offline). Resumes later.")
            return
        end
        local rows = readWindow()
        if rows == nil then
            finish("Could not read statistics database.")
            return
        end
        if #rows == 0 then
            finish(string.format("Reading history synced (%d records).", sent))
            return
        end
        -- Send only the books this window references, not the whole table
        -- with every chunk. Rows whose book row has vanished are still sent
        -- (server skips them, same as before) but never block the cursor.
        local chunk_books, seen = {{}}, {{}}
        local payload = {{}}
        for i = 1, #rows do
            local r = rows[i]
            if books_by_id[r.ko_id] and not seen[r.ko_id] then
                seen[r.ko_id] = true
                chunk_books[#chunk_books + 1] = books_by_id[r.ko_id]
            end
            payload[#payload + 1] = {{ ko_id = r.ko_id, page = r.page, start_time = r.start_time,
                                       duration = r.duration, total_pages = r.total_pages }}
        end
        local resp = apiRequest("POST", "/tome-sync/stats/import",
            {{ device = dev, books = chunk_books, page_stats = payload }})
        if type(resp) ~= "table" then
            finish("Reading-history sync interrupted. Resumes next launch.")
            return
        end
        local last = rows[#rows]
        cur_start, cur_rowid = last.start_time, last.rowid
        sent = sent + #rows
        -- Yield to the UI between chunks (the device stays responsive).
        UIManager:scheduleIn(0.05, sendNext)
    end
    if manual then
        UIManager:show(InfoMessage:new{{
            text = string.format("Syncing reading history (%d records)...", total), timeout = 2 }})
    end
    sendNext()
end

function TomeSync:_flushPendingSessions()
    if #self.pending_sessions == 0 then return end
    if not NetworkMgr:isConnected() then return end

    local remaining = {{}}
    for _, session in ipairs(self.pending_sessions) do
        local ok, result, code = pcall(apiRequest, "POST", "/tome-sync/session", session)
        if not ok or not result or (type(code) == "number" and code >= 300) then
            table.insert(remaining, session)
        end
    end

    self.pending_sessions = remaining
    self:_saveState("tomesync_pending_sessions", remaining)
    if #remaining == 0 then
        logger.info("TomeSync: all pending sessions flushed")
    else
        logger.info("TomeSync:", #remaining, "sessions still pending")
    end
end

function TomeSync:onCloseDocument()
    UIManager:unschedule(self._heartbeat_task)
    self._heartbeat_armed = false
    if not self.enabled or not self.book_id then return end

    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration, session_end = self:_sessionTotals(os.time())
    local dev      = deviceName()

    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = cfi, percentage = pct, device = dev,
    }})

    -- Flush + merge highlights/notes before the book closes.
    pcall(function() self:_syncAnnotations() end)
    -- Push a rating the reader set this session up to Tome before we drop book_id.
    pcall(function() self:_pushRatingOnLeave() end)

    if duration > 10 then
        local uuid = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev)
        pcall(apiRequest, "POST", "/tome-sync/session", {{
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", session_end),
            duration_seconds = duration,
            progress_start   = self.progress_start,
            progress_end     = pct,
            pages_turned     = self.page_count,
            device           = dev,
            session_uuid     = uuid,
        }})
    end

    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = nil
    self.progress_start = nil
    self.last_progress  = nil
    last_session_init.book_id = nil
end

-- ── Helpers ──────────────────────────────────────────────────────────────────

function TomeSync:_tryResolve()
    local doc = self.ui and self.ui.document
    if not doc then return end
    local filename = doc.file:match("([^/]+)$") or doc.file
    -- KOReader's own file identity, computed FRESH from the file (~12KB of
    -- reads). The sidecar's partial_md5_checksum is only a fallback: metadata
    -- archive restores can inherit it from ANOTHER copy of the book, so it is
    -- not a reliable hash of these bytes (observed in emulator testing). The
    -- server matches against the hashes recorded when it scanned or served
    -- the artifact; the filename heuristics remain as final fallback.
    local mok, md5 = pcall(function() return require("util").partialMD5(doc.file) end)
    if not mok or type(md5) ~= "string" then
        md5 = self.ui.doc_settings and self.ui.doc_settings:readSetting("partial_md5_checksum")
    end
    logger.info("TomeSync: resolving filename:", filename)
    local rok, result, rcode = pcall(apiRequest, "GET",
        "/tome-sync/resolve?filename=" .. urlEncode(filename)
        .. (md5 and ("&ko_md5=" .. urlEncode(md5)) or ""))
    if rok and result and type(rcode) == "number" and rcode == 200 and result.book_id then
        self.book_id = result.book_id
        self.book_map[doc.file] = self.book_id
        self:_saveState("tomesync_book_map", self.book_map)
        logger.info("TomeSync: resolved to book_id", self.book_id)
    else
        logger.dbg("TomeSync: could not resolve", filename)
    end
end

function TomeSync:_initSession()
    if self.book_id == last_session_init.book_id
            and (os.time() - last_session_init.at) < 5 then
        logger.dbg("TomeSync: duplicate session init skipped, id =", self.book_id)
        return
    end
    last_session_init.book_id = self.book_id
    last_session_init.at = os.time()

    logger.dbg("TomeSync: book opened, id =", self.book_id)
    self.session_start  = os.time()
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = self.session_start

    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. self.book_id)
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        -- Pull-conflict strategy (like stock kosync): forward and backward
        -- pulls each get prompt / silent / never. Defaults keep the historic
        -- behavior: forward silent, backward never.
        local mode = nil
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            mode = G_reader_settings:readSetting("tomesync_pull_forward") or "silent"
        elseif server_pct < (local_pct - 0.01) and server_pct > 0.01 then
            mode = G_reader_settings:readSetting("tomesync_pull_backward") or "never"
        end
        if mode == "silent" then
            self.progress_start = server_pct
            -- Must be a toast (Notification), not an InfoMessage: this shows
            -- right when Profiles auto-exec ("on book opening") dispatches its
            -- actions, and UIManager:sendEvent drops events at the topmost
            -- non-toast window — an InfoMessage here silently eats the user's
            -- layout profile (font size, margins, columns).
            UIManager:show(Notification:new{{
                text = string.format(
                    "TomeSync: Server at %.0f%% (device: %.0f%%).",
                    server_pct * 100, local_pct * 100
                ),
                timeout = 3,
            }})
            self:_gotoServerPosition(pos, server_pct)
        elseif mode == "prompt" then
            self.progress_start = local_pct
            -- Deferred: a ConfirmBox is a non-toast window, and showing one at
            -- open time would eat the Profiles auto-exec dispatch exactly like
            -- the InfoMessage bug above. 1.5s lets the open settle first.
            UIManager:scheduleIn(1.5, function()
                if not self.ui or not self.ui.document then return end
                UIManager:show(ConfirmBox:new{{
                    text = string.format(
                        "TomeSync: Server position is at %.0f%% (this device: %.0f%%).\\nJump there?",
                        server_pct * 100, local_pct * 100
                    ),
                    ok_text = "Jump",
                    ok_callback = function()
                        self.progress_start = server_pct
                        self:_gotoServerPosition(pos, server_pct)
                    end,
                }})
            end)
        else
            self.progress_start = local_pct
        end
    else
        self.progress_start = self:_getCurrentPercentage()
    end
    self.last_progress = self.progress_start
end

function TomeSync:_gotoServerPosition(pos, server_pct)
    if not (self.ui and self.ui.rolling) then return end
    if type(pos.progress) == "string" and pos.progress:sub(1, 1) == "/" then
        pcall(function()
            self.ui.rolling:onGotoXPointer(pos.progress, pos.progress)
        end)
    else
        -- Not a crengine xpointer (e.g. the web reader stores a
        -- foliate epubcfi here) — onGotoXPointer with it lands on
        -- page 1, so jump by percentage instead.
        pcall(function()
            self.ui.rolling:onGotoPercent(server_pct * 100)
        end)
    end
end

function TomeSync:_getCurrentPercentage()
    if not self.ui or not self.ui.document then return 0 end
    local ok, result = pcall(function()
        if self.ui.document.info.has_pages then
            return self.ui.paging:getLastPercent()
        else
            return self.ui.rolling:getLastPercent()
        end
    end)
    return (ok and result) or 0
end

function TomeSync:_getCurrentProgress()
    if not self.ui or not self.ui.document then return nil end
    local ok, result = pcall(function()
        if self.ui.document.info.has_pages then
            return tostring(self.ui.paging:getLastProgress())
        else
            return self.ui.rolling:getLastProgress()
        end
    end)
    return ok and result or nil
end

function TomeSync:_pushPosition()
    local pct = self:_getCurrentPercentage()
    self.last_progress = pct
    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = self:_getCurrentProgress(), percentage = pct, device = deviceName(),
    }})
end

-- "Sync now" position sync (issue #175): pull first, then push. This was
-- push-only, so a device that was behind overwrote newer progress from
-- another device on the server. The pull reuses the same conflict strategy
-- as book open (forward silent/prompt, backward never, by default); the push
-- is deferred until the pull has settled — including through the prompt — so
-- the final server state reflects the outcome.
function TomeSync:_syncPositionNow()
    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. self.book_id)
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        -- Shifts the session's start by the jump so the jumped-over range is
        -- not credited as reading (progress made before the jump stays counted).
        local function shiftSessionStart()
            if self.progress_start then
                self.progress_start = math.max(0, math.min(1,
                    self.progress_start + (server_pct - local_pct)))
            end
        end
        local mode = nil
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            mode = G_reader_settings:readSetting("tomesync_pull_forward") or "silent"
        elseif server_pct < (local_pct - 0.01) and server_pct > 0.01 then
            mode = G_reader_settings:readSetting("tomesync_pull_backward") or "never"
        end
        if mode == "silent" then
            shiftSessionStart()
            self:_gotoServerPosition(pos, server_pct)
            self:_pushPosition()
            return
        elseif mode == "prompt" then
            -- Menu context, book already open — no Profiles auto-exec dispatch
            -- to protect here, unlike the open-time prompt.
            UIManager:show(ConfirmBox:new{{
                text = string.format(
                    "TomeSync: Server position is at %.0f%% (this device: %.0f%%).\\nJump there?",
                    server_pct * 100, local_pct * 100
                ),
                ok_text = "Jump",
                ok_callback = function()
                    shiftSessionStart()
                    self:_gotoServerPosition(pos, server_pct)
                    self:_pushPosition()
                end,
                cancel_callback = function()
                    -- Explicit keep-local: the device wins, last-write-wins.
                    self:_pushPosition()
                end,
            }})
            return
        end
    end
    self:_pushPosition()
end

-- ── Rating sync (bidirectional, per open book) ───────────────────────────────
-- KOReader's native Book status screen stores a 1–5 star rating + a free-text
-- review in the per-book sidecar under `summary` (`rating` / `note`). Tome holds
-- the same per-user rating/review on UserBookStatus. We mirror both directions
-- using a saved baseline to tell which side changed since the last reconcile:
--   only device changed  -> push device → Tome
--   only Tome changed     -> write Tome → device sidecar
--   both changed (tie)    -> Tome wins (single source of truth)
-- `summary.status` (reading/complete/abandoned) is left untouched — reading
-- status already syncs via TomeSyncPosition.
--
-- Half-stars (build 31): Tome ratings can be 4.5 etc.; KOReader's sidecar is
-- whole-star, so a pulled half-star is rounded to the NEAREST star for the
-- device (halves round up: 4.5 → 5). The baseline
-- therefore keeps TWO values per book — `remote` (Tome's exact value) and
-- `device` (what we wrote to the sidecar) — so the rounded copy is never
-- mistaken for a local edit and pushed back (which would destroy the half-star
-- server-side). Old single-value baselines ({{rating=...}}) migrate on read.

local function ratingBase(tbl, key)
    local base = tbl[key] or {{}}
    if base.rating ~= nil and base.remote == nil and base.device == nil then
        base.remote, base.device = base.rating, base.rating
        base.rating = nil
    end
    return base
end

local function deviceStars(rating)
    -- Sidecar value for a (possibly half-star) Tome rating: nearest whole
    -- star, halves rounding UP (4.5 → 5).
    if type(rating) ~= "number" then return rating end
    return math.floor(rating + 0.5)
end

-- Read the live sidecar summary's rating/review for the open book.
function TomeSync:_localRating()
    local ds = self.ui and self.ui.doc_settings
    if not ds then return nil end
    local summary = ds:readSetting("summary") or {{}}
    return {{ rating = summary.rating, review = summary.note }}, summary
end

-- Reconcile on open: pull Tome's rating into the device sidecar, or push a
-- device rating that hasn't reached Tome yet (e.g. set last session, offline).
function TomeSync:_pullRatingAtOpen()
    if not self.enabled or not self.book_id then return end
    local loc = self:_localRating()
    if not loc then return end

    local ok, status, code = pcall(apiRequest, "GET", "/tome-sync/rating/" .. self.book_id)
    if not ok or not status or code ~= 200 then return end
    local remote_rating = jval(status.rating)
    local remote_review = jval(status.review)

    local key  = tostring(self.book_id)
    local base = ratingBase(self.rating_baseline, key)
    -- Local edits compare against what we last WROTE to the sidecar (whole
    -- stars); remote changes compare against Tome's exact value. A pulled 4.5
    -- rounded to 4 on the device is neither.
    local local_changed  = (loc.rating ~= base.device) or (loc.review ~= base.review)
    local remote_changed = (remote_rating ~= base.remote) or (remote_review ~= base.review)

    if remote_changed then
        -- Tome changed (and, on a both-changed tie, Tome wins): write the sidecar.
        local device_rating = deviceStars(remote_rating)
        local _, summary = self:_localRating()
        summary.rating   = device_rating
        summary.note     = remote_review
        summary.modified = os.date("%Y-%m-%d")
        self.ui.doc_settings:saveSetting("summary", summary)
        if type(device_rating) == "number" then
            pcall(function()
                require("ui/widget/booklist")
                    .setBookInfoCacheProperty(self.ui.document.file, "rating", device_rating)
            end)
        end
        base.remote, base.device, base.review = remote_rating, device_rating, remote_review
        self.rating_baseline[key] = base
        self:_saveState("tomesync_rating_baseline", self.rating_baseline)
        -- Tome's value supersedes any device rating still queued for this book.
        if self.pending_ratings[key] ~= nil then
            self.pending_ratings[key] = nil
            self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        end
        logger.info("TomeSync: applied Tome rating to device for book", self.book_id)
    elseif local_changed then
        self:_pushRating(loc.rating, loc.review)
    end
end

-- PUT a rating/review for an arbitrary book up to Tome and advance its baseline
-- on success. Returns true on success, false otherwise (offline / server error).
-- nil must go on the wire as JSON null (an absent Lua key would be dropped from
-- the body, leaving Tome's old value in place instead of clearing it). rating is
-- always nil or 1–5, never 0, so `or` is safe.
function TomeSync:_putRating(book_id, rating, review)
    local sok, resp, code = pcall(apiRequest, "PUT",
        "/tome-sync/rating/" .. book_id,
        {{ rating = rating or rapidjson.null, review = review or rapidjson.null }})
    if not (sok and resp and type(code) == "number" and code < 300) then
        return false
    end
    local key = tostring(book_id)
    local base = ratingBase(self.rating_baseline, key)
    -- A device push is whole-star, so remote and device coincide.
    base.remote, base.device, base.review = rating, rating, review
    self.rating_baseline[key] = base
    self:_saveState("tomesync_rating_baseline", self.rating_baseline)
    return true
end

-- Push the open book's rating up to Tome. On failure, persist it to the pending
-- queue so a later flush retries it even if this book is never reopened (the
-- close-time trigger only ever fires for the book you're currently in).
function TomeSync:_pushRating(rating, review)
    if not self.book_id then return end
    local key = tostring(self.book_id)
    if self:_putRating(self.book_id, rating, review) then
        if self.pending_ratings[key] ~= nil then
            self.pending_ratings[key] = nil
            self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        end
        logger.info("TomeSync: pushed device rating to Tome for book", self.book_id)
    else
        self.pending_ratings[key] = {{ rating = rating, review = review }}
        self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        logger.info("TomeSync: rating queued for retry for book", self.book_id)
    end
end

-- Flush ratings that failed to send earlier (set offline, or while the server
-- was unreachable). Mirrors _flushPendingSessions; survives reboots via
-- G_reader_settings. A blind last-write-wins push, same as the close-time path.
function TomeSync:_flushPendingRatings()
    if not next(self.pending_ratings) then return end
    if not NetworkMgr:isConnected() then return end

    local remaining = {{}}
    local flushed = false
    for key, entry in pairs(self.pending_ratings) do
        if self:_putRating(key, entry.rating, entry.review) then
            flushed = true
        else
            remaining[key] = entry
        end
    end
    self.pending_ratings = remaining
    self:_saveState("tomesync_pending_ratings", remaining)
    if flushed then logger.info("TomeSync: pending ratings flushed") end
end

-- Reconcile on close/suspend: if the device rating changed during the session,
-- push it up. Tome-wins at open already settled any conflict, so a divergence
-- here is a fresh on-device edit and is safe to send.
function TomeSync:_pushRatingOnLeave()
    if not self.enabled or not self.book_id then return end
    local loc = self:_localRating()
    if not loc then return end
    local base = ratingBase(self.rating_baseline, tostring(self.book_id))
    -- Compare against what we wrote to the sidecar (whole stars): a rounded
    -- half-star pull must not read as a local edit.
    if loc.rating == base.device and loc.review == base.review then return end
    self:_pushRating(loc.rating, loc.review)
end

-- ── Annotation sync (bidirectional: KOReader <-> Tome <-> KOReader) ──────────
-- Identity is the anchor (pos0 xPointer). Edits resolve last-write-wins by
-- KOReader's modification time; deletes use server tombstones + a per-book
-- baseline so a highlight removed on one device can't be resurrected by another's
-- stale copy. All timestamps are KOReader local wall-clock strings (sortable).

local function annotMtime(a)
    return a.datetime_updated or a.datetime or ""
end

local function annotAnchor(a)
    -- pos0 is an xPointer string for EPUB (stable identity); PDF uses table
    -- positions, so fall back to the creation datetime (can't render cross-device).
    return (type(a.pos0) == "string" and a.pos0) or a.datetime
end

local function isWebAnchor(anchor)
    -- Provisional anchor of a highlight created in Tome's web reader; carries no
    -- usable position. This device "adopts" it by locating the text natively.
    return type(anchor) == "string" and anchor:sub(1, 4) == "web:"
end

function TomeSync:_annotItem(a)
    local anchor = annotAnchor(a)
    if not anchor then return nil end
    return {{
        anchor           = anchor,
        anchor_end       = (type(a.pos1) == "string" and a.pos1) or nil,
        highlighted_text = a.text,
        note             = a.note,
        chapter          = a.chapter,
        color            = a.color,
        datetime         = a.datetime,
        datetime_updated = a.datetime_updated,
    }}
end

function TomeSync:_localAnnotationMap()
    -- anchor -> {{ item = <koreader annotation>, mtime }}; nil if module unavailable
    -- (so we never sync a state we can't read).
    local ann = self.ui and self.ui.annotation
    local list = ann and ann.annotations
    if type(list) ~= "table" then return nil end
    local map = {{}}
    for _, a in ipairs(list) do
        local anchor = annotAnchor(a)
        if anchor then
            -- Repaired foreign highlights render at a local position but keep
            -- their SERVER identity: index them under the server anchor so
            -- edits/deletes/tombstones from other devices reach them, and our
            -- own pushes never mint a duplicate identity for the same words.
            local alias = self.repair_map and self.book_id
                and self.repair_map[tostring(self.book_id) .. "|" .. anchor]
            map[(alias and alias.anchor) or anchor] = {{ item = a, mtime = annotMtime(a) }}
        end
    end
    return map
end

function TomeSync:_applyServerState(alive, tombstones)
    -- Merge the server's reconciled state into the local annotation set so this
    -- device shows highlights made on other devices, and drops ones deleted there.
    local ann = self.ui and self.ui.annotation
    if not ann or type(ann.annotations) ~= "table" then return end
    local changed = false
    local localmap = self:_localAnnotationMap() or {{}}

    -- Clamp incoming stamps to this device's clock: server-minted stamps
    -- already arrive shifted into our frame (device_time), but stamps from a
    -- third device with a fast clock can still be "in the future" here — and a
    -- future stamp stored locally would outrank every later local edit. Never
    -- store or compare a stamp ahead of now.
    local device_now = os.date("%Y-%m-%d %H:%M:%S")
    local function clampStamp(stamp)
        if type(stamp) == "string" and stamp > device_now then return device_now end
        return stamp
    end

    local pending_web = {{}}
    for _, s in ipairs(alive or {{}}) do
        if isWebAnchor(s.anchor) then
            -- Not a real position — never addItem it; adopt it below instead.
            table.insert(pending_web, s)
        elseif s.anchor then
            s.datetime         = clampStamp(s.datetime)
            s.datetime_updated = clampStamp(s.datetime_updated)
            local L = localmap[s.anchor]
            local smtime = s.datetime_updated or s.datetime or ""
            if not L then
                -- New highlight from another device: verify it reproduces its
                -- text on THIS copy before drawing; repair or skip otherwise
                -- (paging docs reconstruct nothing — see _applyForeign).
                changed = self:_applyForeign(ann, s) or changed
            elseif smtime > L.mtime then
                -- Newer edit from elsewhere wins (note/color/text).
                L.item.text  = s.highlighted_text
                L.item.note  = s.note
                L.item.color = s.color
                L.item.datetime_updated = s.datetime_updated
                changed = true
            end
        end
    end

    for _, t in ipairs(tombstones or {{}}) do
        local map2 = self:_localAnnotationMap() or {{}}
        local L = map2[t.anchor]
        if L and L.mtime <= clampStamp(t.deleted_at or "") then
            for i = #ann.annotations, 1, -1 do
                if ann.annotations[i] == L.item then
                    table.remove(ann.annotations, i); changed = true; break
                end
            end
        end
    end

    if #pending_web > 0 then
        local adopted = self:_adoptWebAnnotations(pending_web)
        changed = changed or (adopted > 0)
    end

    if changed then
        pcall(function() self.ui:handleEvent(Event:new("AnnotationsModified", {{ nb_highlights_added = 0 }})) end)
        pcall(function() UIManager:setDirty(self.ui.dialog, "full") end)
    end
end

function TomeSync:_locateText(text, chapter)
    -- Find `text` in the open (rolling) document; when several places carry
    -- the same words prefer the hit inside `chapter`. Returns start/end
    -- xPointers or nil. Shared by web adoption and foreign-highlight repair.
    local ok, results = pcall(function()
        -- (pattern, case_insensitive, nb_context_words, max_hits, regex)
        return self.ui.document:findAllText(text, false, 2, 5, false)
    end)
    if not ok or type(results) ~= "table" then return nil end
    local hit = results[1]
    if #results > 1 and chapter then
        for _, r in ipairs(results) do
            local okc, title = pcall(function()
                local page = self.ui.document:getPageFromXPointer(r.start)
                return self.ui.toc:getTocTitleByPage(page)
            end)
            if okc and title == chapter then hit = r; break end
        end
    end
    if hit and type(hit.start) == "string" and type(hit["end"]) == "string" then
        return hit.start, hit["end"]
    end
    return nil
end

function TomeSync:_applyForeign(ann, s)
    -- A highlight made on another device. NEVER paint it on the wrong words:
    -- verify that the anchor reproduces the highlighted text on THIS copy of
    -- the book, repair by text search when it doesn't (a different bake of the
    -- same book shifts xPointers), and skip entirely when the text can't be
    -- located. Repairs keep the SERVER identity (repair_map) so the origin
    -- device's anchor is never rewritten — no cross-device anchor ping-pong.
    local function norm(t)
        if type(t) ~= "string" then return nil end
        t = t:gsub("\194\173", "")       -- soft hyphens (U+00AD)
        t = t:gsub("%s+", " ")
        return t:match("^%s*(.-)%s*$")
    end
    local function add(p0, p1)
        return pcall(function()
            ann:addItem({{
                page = p0, pos0 = p0, pos1 = p1,
                text = s.highlighted_text, note = s.note, chapter = s.chapter,
                color = s.color, drawer = "lighten",
                datetime = s.datetime, datetime_updated = s.datetime_updated,
            }})
        end) == true
    end

    if not self.ui.rolling then
        -- Paging (PDF/CBZ) docs can't reconstruct foreign annotations at all:
        -- they need a numeric page and rect tables, which sync anchors don't
        -- carry — planting strings corrupts the annotation list and crashes
        -- the reader on repaint. They stay server-side only (skipped items
        -- never enter the local map or baseline, so nothing is resurrected
        -- or spuriously deleted).
        return false
    end

    local want = norm(s.highlighted_text)
    if s.anchor:sub(1, 5) == "/body" then
        local okx, got = pcall(function()
            return self.ui.document:getTextFromXPointers(s.anchor, s.anchor_end or s.anchor)
        end)
        if okx and (not want or norm(got) == want) then
            -- verified (or nothing to verify against): draw at the real anchor
            return add(s.anchor, s.anchor_end or s.anchor)
        end
    end

    if not want or want == "" then return false end
    local p0, p1 = self:_locateText(s.highlighted_text, s.chapter)
    if not p0 then return false end   -- unlocatable here: skip, never wrong words
    for _, a in ipairs(ann.annotations or {{}}) do
        if a.pos0 == p0 then return false end   -- already rendered by an earlier repair
    end
    if not add(p0, p1) then return false end
    self.repair_map[tostring(self.book_id) .. "|" .. p0] = {{ anchor = s.anchor, anchor_end = s.anchor_end }}
    self:_saveState("tomesync_repair_map", self.repair_map)
    logger.info("TomeSync: repaired foreign highlight to", p0)
    return true
end

function TomeSync:_adoptWebAnnotations(pending)
    -- Highlights created in Tome's web reader arrive with a provisional "web:"
    -- anchor and no position. Locate each one's text with crengine and create a
    -- NATIVE annotation (real xPointers); the next push carries adopted_from so
    -- the server retires the provisional row. Anchors are deterministic per book
    -- copy, so two devices adopting the same highlight converge on one anchor.
    -- EPUB (rolling) only — PDF positions aren't xPointer strings.
    local ann = self.ui and self.ui.annotation
    if not ann or not self.ui.rolling or not self.ui.document then return 0 end
    local adopted = 0
    -- Text-level dedupe: a passage already highlighted locally (e.g. adopted on a
    -- previous pull whose push failed) must not be adopted twice.
    local seen_text = {{}}
    for _, a in ipairs(ann.annotations or {{}}) do
        if a.text then seen_text[a.text] = true end
    end
    for _, s in ipairs(pending) do
        local text = s.highlighted_text
        if type(text) == "string" and text ~= "" and not seen_text[text] then
            local hit_start, hit_end = self:_locateText(text, s.chapter)
            if hit_start then
                local okAdd = pcall(function()
                    ann:addItem({{
                        page = hit_start, pos0 = hit_start, pos1 = hit_end,
                        text = text, note = s.note, chapter = s.chapter,
                        color = s.color, drawer = "lighten",
                        datetime = s.datetime, datetime_updated = s.datetime_updated,
                    }})
                end)
                if okAdd then
                    seen_text[text] = true
                    self.adopt_pending[hit_start] = s.anchor
                    adopted = adopted + 1
                end
            end
            -- Unlocatable text (e.g. selection spanning a page-break element):
            -- leave the provisional alone — it stays web-only, never wrong.
        end
    end
    if adopted > 0 then
        self:_saveState("tomesync_adopt_pending", self.adopt_pending)
    end
    return adopted
end

function TomeSync:_syncAnnotations()
    -- Push local changes (diff vs baseline) and pull everyone else's, in one call.
    if not self.book_id then return nil end
    local localmap = self:_localAnnotationMap()
    if not localmap then return nil end
    local bk = tostring(self.book_id)
    local baseline = self.annot_baseline[bk] or {{}}

    -- Bind the baseline to THIS sidecar instance. A fresh sidecar (new
    -- download of the book, or a wiped sidecar) starts without our marker:
    -- its empty annotation list reflects a NEW FILE, not the user deleting
    -- every highlight — diffing the old baseline against it would push
    -- deletes and tombstone the book's highlights server-side (observed live:
    -- re-downloading a book wiped its synced highlight). Reset the baseline
    -- instead; the pull below re-applies the server state. True deletions
    -- still propagate: once the marker is set, baseline diffs work as before.
    if self.ui.doc_settings then
        if not self.ui.doc_settings:readSetting("tomesync_annot_bound") and next(baseline) ~= nil then
            baseline = {{}}
            self.annot_baseline[bk] = {{}}
        end
        self.ui.doc_settings:saveSetting("tomesync_annot_bound", true)
    end

    -- Future-watermark guard: no local stamp may sit ahead of this device's
    -- clock. Future stamps arrive via server-minted datetimes applied before
    -- the clock-offset guard existed (or a third device's fast clock) and would
    -- outrank every later local edit until the clock catches up. Clamp the
    -- annotation AND its baseline entry to now — equal values, so no spurious
    -- re-push, and the user's next edit is strictly newer again.
    local now = os.date("%Y-%m-%d %H:%M:%S")   -- local wall-clock, matches KOReader's
    for anchor, L in pairs(localmap) do
        if L.mtime > now then
            if L.item.datetime_updated and L.item.datetime_updated > now then
                L.item.datetime_updated = now
            end
            if L.item.datetime and L.item.datetime > now then
                L.item.datetime = now
            end
            L.mtime = annotMtime(L.item)
            if baseline[anchor] ~= nil then baseline[anchor] = L.mtime end
        end
    end
    for anchor, m in pairs(baseline) do
        if m > now then baseline[anchor] = now end
    end

    local upserts, deletes = {{}}, {{}}
    for anchor, L in pairs(localmap) do
        if baseline[anchor] == nil or baseline[anchor] ~= L.mtime then
            local it = self:_annotItem(L.item)
            if it then
                if it.anchor ~= anchor then
                    -- repaired item: push under its server identity, with the
                    -- ORIGIN device's positions (ours are local rendering only)
                    local alias = self.repair_map[bk .. "|" .. it.anchor]
                    it.anchor = anchor
                    it.anchor_end = (alias and alias.anchor_end) or it.anchor_end
                end
                table.insert(upserts, it)
            end
        end
    end
    for anchor, _ in pairs(baseline) do
        if localmap[anchor] == nil then
            table.insert(deletes, {{ anchor = anchor, datetime = now }})
        end
    end

    -- device_time lets the server shift its own (web-minted) stamps into THIS
    -- device's clock frame — see the server's clock-offset guard.
    local resp = apiRequest("POST", "/tome-sync/annotations/" .. self.book_id .. "/sync",
                            {{ upserts = upserts, deletes = deletes,
                               device_time = os.date("%Y-%m-%d %H:%M:%S") }})
    if not resp then return nil end   -- offline/failed: keep baseline so we retry

    self:_applyServerState(resp.annotations, resp.tombstones)

    -- Push freshly-adopted web annotations right away (adopted_from tells the
    -- server to retire the provisional). On failure adopt_pending persists, so
    -- the next sync retries — the baseline alone would swallow the adoption.
    local adopts = {{}}
    local after1 = self:_localAnnotationMap() or {{}}
    for anchor, prov in pairs(self.adopt_pending) do
        local L = after1[anchor]
        if L then
            local it = self:_annotItem(L.item)
            if it then it.adopted_from = prov; table.insert(adopts, it) end
        else
            self.adopt_pending[anchor] = nil   -- adopted copy gone locally; drop
        end
    end
    if #adopts > 0 then
        local resp2 = apiRequest("POST", "/tome-sync/annotations/" .. self.book_id .. "/sync",
                                 {{ upserts = adopts, deletes = {{}} }})
        if resp2 then
            for _, it in ipairs(adopts) do self.adopt_pending[it.anchor] = nil end
        end
    end
    self:_saveState("tomesync_adopt_pending", self.adopt_pending)

    -- Rebuild the baseline from the post-merge local state.
    local newbase = {{}}
    local after = self:_localAnnotationMap() or {{}}
    for anchor, L in pairs(after) do newbase[anchor] = L.mtime end
    self.annot_baseline[bk] = newbase
    self:_saveState("tomesync_annot_baseline", self.annot_baseline)

    -- Drop aliases whose local rendering is gone (a local delete already went
    -- out under the server identity above) so the map can't grow stale.
    local raw = {{}}
    for _, a in ipairs((self.ui.annotation and self.ui.annotation.annotations) or {{}}) do
        if type(a.pos0) == "string" then raw[a.pos0] = true end
    end
    local pruned = false
    local prefix = bk .. "|"
    for key in pairs(self.repair_map) do
        if key:sub(1, #prefix) == prefix and not raw[key:sub(#prefix + 1)] then
            self.repair_map[key] = nil; pruned = true
        end
    end
    if pruned then self:_saveState("tomesync_repair_map", self.repair_map) end
    return resp
end

function TomeSync:registerBookId(file_path, book_id)
    self.book_map[file_path] = book_id
    self:_saveState("tomesync_book_map", self.book_map)
    logger.info("TomeSync: registered book_id", book_id, "for", file_path)
end

-- ── Series download ─────────────────────────────────────────────────────────

function TomeSync:_downloadSeriesBooks(series_name, books, min_index, book_type, quiet)
    -- `quiet` suppresses the summary popups (used by the inbox, which shows its
    -- own roll-up). Returns {{downloaded, skipped, failed}} so callers can tell
    -- success (file is now on device) from failure.
    -- The server sends "__unserialized__" as the No Series sentinel. Standalone
    -- books are filed per-author (matching Tome's own library layout), so there is
    -- no single folder for the bucket — "No Series" is only a popup label.
    local is_no_series = (series_name == "__unserialized__")
    local batch_label  = is_no_series and "No Series" or series_name

    -- home_dir is the user-set library root (File Manager → long-press → "Set as HOME").
    -- Fall back to download_dir / lastdir for installs where home_dir isn't set.
    local base_dir = G_reader_settings:readSetting("home_dir")
                  or G_reader_settings:readSetting("download_dir")
                  or G_reader_settings:readSetting("lastdir")
    if not base_dir then
        UIManager:show(InfoMessage:new{{
            text = "No download directory configured.",
            timeout = 4,
        }})
        return {{ downloaded = 0, skipped = 0, failed = #books }}
    end

    -- A user template (Settings → Download location & naming) replaces the
    -- built-in layout below; empty/unset means built-in.
    local template = G_reader_settings:readSetting("tomesync_download_template") or ""

    -- Built-in layout: organize by book-type subfolder. A real series shares
    -- one folder; the No Series bucket files each book under its author
    -- (resolved per book below). Dirs are created lazily so a template run
    -- doesn't leave empty default folders behind. The type is resolved PER BOOK
    -- (book.book_type), not from the batch: the No Series bucket mixes types, so
    -- a single batch type would misfile standalone books (issue #88). The batch
    -- `book_type` is only a fallback for older servers that omit the per-book field.
    local function ensureDefaultDirs(effective_type)
        local type_dir = base_dir .. "/" .. effective_type
        lfs.mkdir(type_dir)
        local series_dir
        if not is_no_series then
            series_dir = type_dir .. "/" .. util.getSafeFilename(series_name)
            lfs.mkdir(series_dir)
        end
        return type_dir, series_dir
    end

    -- Build reverse lookup: book_id → local path (to skip already-downloaded books)
    local id_to_path = {{}}
    for path, bid in pairs(self.book_map) do
        id_to_path[bid] = path
    end

    -- Pre-compute the download queue so progress counts only real work.
    local queue = {{}}
    local skipped = 0
    -- Representative "saved to" folder for the summary popup. With per-book
    -- types the No Series bucket can span several folders, so this is just the
    -- first real destination — a sensible "look here" pointer, not the only one.
    local save_location
    for _, book in ipairs(books) do
        if min_index and type(book.series_index) == "number" and book.series_index <= min_index then
            skipped = skipped + 1
        elseif id_to_path[book.id] and lfs.attributes(id_to_path[book.id]) then
            skipped = skipped + 1
        else
            local file = pickBestFile(book.files)
            if not file then
                table.insert(queue, {{book = book, file = nil, dest = nil}})
            else
                local ext = file.format or "epub"
                -- Per-book type (server >= build 24); fall back to the batch
                -- type for older servers. This is what fixes mixed-type No
                -- Series downloads landing in the wrong folder (issue #88).
                local effective_type = (type(book.book_type) == "string"
                                        and book.book_type ~= "" and book.book_type)
                                        or book_type or "book"
                local dest
                if template ~= "" then
                    local rel = renderDownloadPath(template, {{
                        book_type = effective_type,
                        series    = (not is_no_series) and series_name or "",
                        volume    = type(book.series_index) == "number"
                                    and book.series_index or nil,
                        title     = book.title or "",
                        author    = type(book.author) == "string" and book.author or "",
                    }})
                    if rel then
                        dest = base_dir .. "/" .. rel .. "." .. ext
                        mkdirp(dest:match("^(.*)/[^/]+$"))
                    end
                end
                if not dest then
                    -- Built-in layout (also the fallback when a template
                    -- renders to nothing usable for this book).
                    local type_dir, series_dir = ensureDefaultDirs(effective_type)
                    local display_title
                    if type(book.series_index) == "number" then
                        local vol = book.series_index
                        if vol == math.floor(vol) then vol = math.floor(vol) end
                        display_title = "Vol. " .. tostring(vol) .. " — " .. book.title
                    else
                        display_title = book.title
                    end
                    local fname = util.getSafeFilename(display_title .. "." .. ext)
                    -- Real series → shared series_dir. No Series → per-author folder,
                    -- falling back to the type dir when the book has no author.
                    local dest_dir = series_dir
                    if is_no_series then
                        if type(book.author) == "string" and book.author ~= "" then
                            dest_dir = type_dir .. "/" .. util.getSafeFilename(book.author)
                            lfs.mkdir(dest_dir)
                        else
                            dest_dir = type_dir
                        end
                    end
                    dest = dest_dir .. "/" .. fname
                end
                if lfs.attributes(dest) then
                    skipped = skipped + 1
                else
                    save_location = save_location or dest:match("^(.*)/[^/]+$")
                    table.insert(queue, {{book = book, file = file, dest = dest}})
                end
            end
        end
    end

    if #queue == 0 then
        if not quiet then
            UIManager:show(InfoMessage:new{{
                text = string.format(
                    "%s\\n\\nNothing to download.\\nSkipped: %d",
                    batch_label, skipped
                ),
                timeout = 5,
            }})
        end
        -- Nothing queued means every book is already on disk → success.
        return {{ downloaded = 0, skipped = skipped, failed = 0 }}
    end

    -- Live progress popup — replace the message between each book.
    -- forceRePaint guarantees the widget is drawn before the blocking HTTP call.
    local progress_msg
    local function showProgress(text)
        if progress_msg then UIManager:close(progress_msg) end
        progress_msg = InfoMessage:new{{ text = text }}
        UIManager:show(progress_msg)
        UIManager:forceRePaint()
    end

    local function fmtMB(n)
        return string.format("%.1f MB", n / 1048576)
    end
    local downloaded, failed = 0, 0
    local failed_books = {{}}
    for i, item in ipairs(queue) do
        local head = string.format("%s\\n\\nDownloading %d of %d\\n%s",
                                   batch_label, i, #queue, item.book.title)
        showProgress(head)
        if not item.file then
            failed = failed + 1
            table.insert(failed_books, item.book)
        else
            local total = type(item.file.file_size) == "number" and item.file.file_size or nil
            local ok, err = downloadFile(item.book.id, item.file.id, item.dest, total,
                function(received, size)
                    if size then
                        showProgress(string.format("%s\\n%d%% of %s", head,
                            math.floor(received * 100 / size), fmtMB(size)))
                    else
                        showProgress(string.format("%s\\n%s", head, fmtMB(received)))
                    end
                end)
            if ok then
                downloaded = downloaded + 1
                self.book_map[item.dest] = item.book.id
            else
                logger.warn("TomeSync: download failed for", item.book.title, err)
                failed = failed + 1
                table.insert(failed_books, item.book)
            end
        end
    end
    if progress_msg then UIManager:close(progress_msg) end

    -- Persist book_map
    self:_saveState("tomesync_book_map", self.book_map)

    if not quiet then
        if failed > 0 and #failed_books > 0 then
            -- Offer a retry instead of a one-shot failure count: transient
            -- WiFi drops are the usual culprit and a second pass fixes them.
            UIManager:show(ConfirmBox:new{{
                text = string.format(
                    "%s\\n\\nDownloaded: %d\\nSkipped: %d\\nFailed: %d",
                    batch_label, downloaded, skipped, failed
                ),
                ok_text = "Retry failed",
                cancel_text = "Close",
                ok_callback = function()
                    self:_downloadSeriesBooks(series_name, failed_books, min_index, book_type, quiet)
                end,
            }})
        else
            UIManager:show(InfoMessage:new{{
                text = string.format(
                    "%s\\n\\nDownloaded: %d\\nSkipped: %d\\nFailed: %d\\n\\nSaved to: %s",
                    batch_label, downloaded, skipped, failed, save_location or base_dir
                ),
                timeout = 8,
            }})
            if downloaded == 1 and #queue == 1 and queue[1].dest then
                -- Single fresh download: offer to jump straight into it.
                local dest = queue[1].dest
                UIManager:show(ConfirmBox:new{{
                    text = string.format('Open "%s" now?', queue[1].book.title or "book"),
                    ok_text = "Open",
                    cancel_text = "Later",
                    ok_callback = function()
                        require("apps/reader/readerui"):showReader(dest)
                    end,
                }})
            end
        end
    end
    return {{ downloaded = downloaded, skipped = skipped, failed = failed }}
end

-- Shared drill-down list: a "Download all" row plus one row per book. Used by
-- the series browser, the author browser, and search results. Tap downloads a
-- book; hold sets its read status. `data`:
--   title        menu title
--   books        list of server book entries
--   series_name  filing default for books without their own series (optional)
--   book_type    filing fallback for older servers (optional)
--   mixed        true → prefix each row with the book's own series
--   reload       called after a status write-back to rebuild with fresh data
function TomeSync:_bookListMenu(data)
    local items = {{}}
    table.insert(items, {{
        text     = string.format("Download all (%d)", #data.books),
        callback = function() self:_downloadListBooks(data, nil) end,
    }})
    -- book_id → on-device path (same signal the download queue uses to skip)
    local id_to_path = {{}}
    for path, bid in pairs(self.book_map) do
        if lfs.attributes(path) then id_to_path[bid] = path end
    end
    for _, book in ipairs(data.books) do
        local label
        if type(book.series_index) == "number" then
            local vol = book.series_index
            if vol == math.floor(vol) then vol = math.floor(vol) end
            label = "Vol. " .. tostring(vol) .. " — " .. book.title
        else
            label = book.title
        end
        if data.mixed and type(book.series) == "string" and book.series ~= "" then
            label = book.series .. " · " .. label
        end
        if id_to_path[book.id] then
            label = label .. "  · on device"
        end
        -- Status marker (build 33): only for the non-default states.
        if book.status == "reading" or book.status == "read" then
            label = label .. "  · " .. book.status
        end
        table.insert(items, {{
            text     = label,
            book     = book,
            callback = function() self:_downloadListBooks(data, book) end,
        }})
    end

    local menu
    menu = Menu:new{{
        title       = data.title,
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }}
    -- Hold a book row to set its read status (write-back to Tome).
    menu.onMenuHold = function(_, item)
        if item.book then
            self:_statusDialog(item.book, function()
                UIManager:close(menu)
                if data.reload then data.reload() end
            end)
        end
        return true
    end
    UIManager:show(menu)
end

-- Route one book (or the whole list) into the download machinery with the
-- right filing identity: a book with its own series files under that series;
-- a standalone files like the No Series bucket (book-type/author folders).
function TomeSync:_downloadListBooks(data, book)
    local function seriesOf(b)
        if type(b.series) == "string" and b.series ~= "" then return b.series end
        return data.series_name or "__unserialized__"
    end
    if book then
        self:_downloadSeriesBooks(seriesOf(book), {{ book }}, nil,
                                  book.book_type or data.book_type)
        return
    end
    if data.series_name then
        -- Homogeneous list (one series / the No Series bucket): one batch.
        self:_downloadSeriesBooks(data.series_name, data.books, nil, data.book_type)
        return
    end
    -- Mixed list (author / search): file each book by its own identity, then
    -- roll the counts up into one summary.
    local downloaded, skipped, failed = 0, 0, 0
    for _, b in ipairs(data.books) do
        local r = self:_downloadSeriesBooks(seriesOf(b), {{ b }}, nil, b.book_type, true)
        downloaded = downloaded + (r.downloaded or 0)
        skipped    = skipped + (r.skipped or 0)
        failed     = failed + (r.failed or 0)
    end
    UIManager:show(InfoMessage:new{{
        text = string.format("%s\\n\\nDownloaded: %d\\nSkipped: %d\\nFailed: %d",
                             data.title or "Download", downloaded, skipped, failed),
        timeout = 6,
    }})
end

-- Hold-a-row dialog: set unread/reading/read on the server (deliberate user
-- action — unlike telemetry, which only suggests status).
function TomeSync:_statusDialog(book, on_done)
    local dialog
    local function setStatus(status)
        UIManager:close(dialog)
        whenConnected(function()
            local ok, resp, code = pcall(apiRequest, "PUT", "/tome-sync/status/" .. book.id,
                                         {{ status = status }})
            if ok and type(code) == "number" and code < 300 then
                book.status = status
                UIManager:show(Notification:new{{
                    text = string.format('"%s" marked %s.', book.title or "Book", status),
                    timeout = 2,
                }})
                if on_done then on_done() end
            else
                UIManager:show(InfoMessage:new{{
                    text = "Could not update status (" .. tostring(code) .. ").",
                    timeout = 4,
                }})
            end
        end)
    end
    dialog = ButtonDialog:new{{
        title = (book.title or "Book") .. "\\nSet read status",
        buttons = {{
            {{ {{ text = "Unread",  callback = function() setStatus("unread") end }} }},
            {{ {{ text = "Reading", callback = function() setStatus("reading") end }} }},
            {{ {{ text = "Read",    callback = function() setStatus("read") end }} }},
        }},
    }}
    UIManager:show(dialog)
end

-- Adapter kept for the series flow (name/shape unchanged for its callers).
function TomeSync:_seriesBooksMenu(data)
    local display = data.series_name
    if display == "__unserialized__" then display = "No Series" end
    self:_bookListMenu{{
        title       = display,
        books       = data.books,
        series_name = data.series_name,
        book_type   = data.book_type,
        reload      = function()
            if #data.books > 0 then self:_openSeriesBooks(data.books[1].id) end
        end,
    }}
end

-- ── Author browse axis (build 33) ────────────────────────────────────────────

function TomeSync:_authorsMenu()
    whenConnected(function() self:_authorsMenuImpl() end)
end

function TomeSync:_authorsMenuImpl()
    local ok, authors, code = pcall(apiRequest, "GET", "/tome-sync/authors")
    if not ok or type(authors) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load authors.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_authorsMenuImpl() end,
        }})
        return
    end
    local items = {{}}
    for _, a in ipairs(authors) do
        local name = a.name
        if name == "__unknown__" then name = "Unknown author" end
        table.insert(items, {{
            text = name .. " (" .. a.book_count .. ")",
            callback = function() self:_openAuthorBooks(a.name) end,
        }})
    end
    UIManager:show(Menu:new{{
        title = "Authors",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    }})
end

function TomeSync:_openAuthorBooks(author)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/author-books?author=" .. urlEncode(author))
    if ok and type(data) == "table" and data.books then
        local display = author
        if display == "__unknown__" then display = "Unknown author" end
        self:_bookListMenu{{
            title  = display,
            books  = data.books,
            mixed  = true,
            reload = function() self:_openAuthorBooks(author) end,
        }}
    else
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load author's books.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openAuthorBooks(author) end,
        }})
    end
    local _ = code
end

-- ── Shelves axis (build 36) ──────────────────────────────────────────────────

function TomeSync:_shelvesMenu()
    whenConnected(function() self:_shelvesMenuImpl() end)
end

function TomeSync:_shelvesMenuImpl()
    local ok, shelves, code = pcall(apiRequest, "GET", "/tome-sync/shelves")
    if not ok or type(shelves) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load shelves.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_shelvesMenuImpl() end,
        }})
        return
    end
    if #shelves == 0 then
        UIManager:show(InfoMessage:new{{
            text = "No shelves yet.\\nCreate one in the web app - any filtered view can be saved as a shelf.",
            timeout = 5,
        }})
        return
    end
    local items = {{}}
    for _, s in ipairs(shelves) do
        table.insert(items, {{
            text = s.name .. " (" .. s.book_count .. ")",
            callback = function() self:_openShelfBooks(s.id, s.name) end,
        }})
    end
    UIManager:show(Menu:new{{
        title = "Shelves",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    }})
end

function TomeSync:_openShelfBooks(shelf_id, shelf_name)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/shelf-books?shelf_id=" .. tostring(shelf_id))
    if ok and type(data) == "table" and data.books then
        -- Shelves mix series, so books file by their own identity on download.
        self:_bookListMenu{{
            title  = shelf_name,
            books  = data.books,
            mixed  = true,
            reload = function() self:_openShelfBooks(shelf_id, shelf_name) end,
        }}
    else
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load shelf.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openShelfBooks(shelf_id, shelf_name) end,
        }})
    end
    local _ = code
end

-- ── Search from the device (build 33) ────────────────────────────────────────

function TomeSync:_recentSearches()
    return self.state:readSetting("tomesync_recent_searches") or {{}}
end

function TomeSync:_rememberSearch(q)
    local recents = self:_recentSearches()
    for i = #recents, 1, -1 do
        if recents[i] == q then table.remove(recents, i) end
    end
    table.insert(recents, 1, q)
    while #recents > 8 do table.remove(recents) end
    self:_saveState("tomesync_recent_searches", recents)
end

function TomeSync:_searchMenu()
    -- Submit-based input (the right call on e-ink), with recent searches one
    -- tap away underneath.
    local dialog
    dialog = InputDialog:new{{
        title = "Search library",
        input_hint = "title, author, or series",
        buttons = {{{{
            {{ text = "Cancel", id = "close",
              callback = function() UIManager:close(dialog) end }},
            {{ text = "Search", is_enter_default = true,
              callback = function()
                  local q = dialog:getInputText()
                  if q and q:match("%S") then
                      UIManager:close(dialog)
                      self:_runSearch(q)
                  end
              end }},
        }}}},
    }}
    local recents = self:_recentSearches()
    if #recents > 0 then
        -- A second row of up to 3 recent queries; the full list lives in the
        -- results menu title history anyway, and 3 covers the muscle-memory case.
        local row = {{}}
        for i = 1, math.min(3, #recents) do
            local q = recents[i]
            table.insert(row, {{ text = q, callback = function()
                UIManager:close(dialog)
                self:_runSearch(q)
            end }})
        end
        table.insert(dialog.buttons, 1, row)
    end
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function TomeSync:_runSearch(q)
    whenConnected(function()
        local ok, data, code = pcall(apiRequest, "GET",
            "/tome-sync/search?q=" .. urlEncode(q))
        if not ok or type(data) ~= "table" or not data.books
                or (type(code) == "number" and code >= 300) then
            UIManager:show(ConfirmBox:new{{
                text = "Search failed.",
                ok_text = "Retry",
                cancel_text = "Close",
                ok_callback = function() self:_runSearch(q) end,
            }})
            return
        end
        self:_rememberSearch(q)
        if #data.books == 0 then
            UIManager:show(InfoMessage:new{{
                text = 'No results for "' .. q .. '".',
                timeout = 3,
            }})
            return
        end
        local title = string.format('Search: %s (%d)', q, data.total or #data.books)
        if (data.total or 0) > #data.books then
            title = string.format('Search: %s (%d of %d)', q, #data.books, data.total)
        end
        self:_bookListMenu{{
            title  = title,
            books  = data.books,
            mixed  = true,
            reload = function() self:_runSearch(q) end,
        }}
    end)
end

function TomeSync:_browseSeriesMenu()
    whenConnected(function() self:_browseSeriesMenuImpl() end)
end

function TomeSync:_browseSeriesMenuImpl()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{{
            text = "WiFi not connected.",
            timeout = 3,
        }})
        return
    end

    local ok, series_list, code = pcall(apiRequest, "GET", "/tome-sync/series")
    if not ok or type(series_list) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load series list.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_browseSeriesMenuImpl() end,
        }})
        return
    end

    local items = {{}}
    -- Cross-axis entry points (build 33): search and the author axis live at
    -- the top of the browser, so standalones aren't stuck behind "No Series".
    table.insert(items, {{
        text     = "Search library…",
        callback = function() self:_searchMenu() end,
    }})
    table.insert(items, {{
        text     = "Browse by author",
        callback = function() self:_authorsMenu() end,
    }})
    table.insert(items, {{
        text      = "Shelves",
        separator = true,
        callback  = function() self:_shelvesMenu() end,
    }})
    for _, s in ipairs(series_list) do
        local name = s.name
        if name == "__unserialized__" then name = "No Series" end
        local text = name .. " (" .. s.book_count .. ")"
        -- type-check guards against JSON null, which rapidjson decodes to a
        -- truthy userdata sentinel rather than nil.
        if type(s.author) == "string" and s.author ~= "" then
            text = text .. " - " .. s.author
        end
        table.insert(items, {{
            text = text,
            callback = function()
                self:_openSeriesBooks(s.first_book_id)
            end,
        }})
    end

    local menu = Menu:new{{
        title = "Series Browser",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    }}
    UIManager:show(menu)
end

function TomeSync:_openSeriesBooks(first_book_id)
    -- Fetch the books in this series, then drill into a per-book list so a
    -- single title can be downloaded instead of the whole series.
    local ok2, data, code2 = pcall(apiRequest, "GET", "/tome-sync/series/" .. first_book_id)
    if ok2 and type(data) == "table" and data.books then
        self:_seriesBooksMenu(data)
    else
        UIManager:show(ConfirmBox:new{{
            text = "Failed to load series books.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openSeriesBooks(first_book_id) end,
        }})
    end
    local _ = code2
end

function TomeSync:_downloadCurrentBookSeries(rest_only)
    if not self.book_id then
        UIManager:show(InfoMessage:new{{
            text = "No book resolved. Open a book first.",
            timeout = 3,
        }})
        return
    end

    whenConnected(function() self:_downloadCurrentBookSeriesImpl(rest_only) end)
end

function TomeSync:_downloadCurrentBookSeriesImpl(rest_only)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/series/" .. self.book_id)
    if not ok or not data or not data.books then
        UIManager:show(InfoMessage:new{{
            text = "Failed to load series (book may not belong to one).",
            timeout = 4,
        }})
        return
    end

    local min_index = nil
    if rest_only then
        -- Find current book's series_index
        for _, b in ipairs(data.books) do
            if b.id == self.book_id then
                if type(b.series_index) == "number" then
                    min_index = b.series_index
                end
                break
            end
        end
    end

    self:_downloadSeriesBooks(data.series_name, data.books, min_index, data.book_type)
end

-- ── Library sweep: adopt closed books (build 34) ─────────────────────────────
-- Walks the device library for books TomeSync has NEVER synced (pre-Tome
-- history, other launchers, sideloads), resolves them by partial-MD5 in
-- batches, and adopts each sidecar's status/rating/progress. The server fills
-- gaps only, so re-sweeping is always safe. Resumable: every book is ack-gated
-- into a ledger keyed by its sidecar's mtime — re-runs skip everything already
-- taken unless the sidecar changed since.

function TomeSync:_sweepLibrary()
    if sweep_running then
        UIManager:show(InfoMessage:new{{ text = "Library sweep already running.", timeout = 3 }})
        return
    end
    whenConnected(function() self:_sweepLibraryImpl() end)
end

function TomeSync:_sweepLibraryImpl()
    local base_dir = G_reader_settings:readSetting("home_dir")
                  or G_reader_settings:readSetting("download_dir")
                  or G_reader_settings:readSetting("lastdir")
    if not base_dir then
        UIManager:show(InfoMessage:new{{ text = "No home folder configured.", timeout = 4 }})
        return
    end
    local DocSettings = require("docsettings")
    local sweep = self.state:readSetting("tomesync_sweep") or {{}}
    sweep.done = sweep.done or {{}}

    -- Phase 1: walk — never-synced books with a sidecar whose mtime moved past
    -- the ledger. Cheap: no file is opened or hashed here.
    local candidates = {{}}
    local function walk(dir, depth)
        if depth > 10 then return end
        local ok, iter, dirobj = pcall(lfs.dir, dir)
        if not ok then return end
        for entry in iter, dirobj do
            if entry ~= "." and entry ~= ".." and entry:sub(1, 1) ~= "." then
                local path = dir .. "/" .. entry
                local mode = lfs.attributes(path, "mode")
                if mode == "directory" then
                    if not entry:match("%.sdr$") then walk(path, depth + 1) end
                elseif mode == "file" then
                    local ext = entry:match("%.([^.]+)$")
                    if ext and SWEEP_EXTS[ext:lower()] and not self.book_map[path] then
                        local sidecar = DocSettings:findSidecarFile(path)
                        if sidecar then
                            local smtime = lfs.attributes(sidecar, "modification") or 0
                            if (sweep.done[path] or -1) < smtime then
                                table.insert(candidates, {{ path = path, smtime = smtime }})
                            end
                        end
                    end
                end
            end
        end
    end
    walk(base_dir, 0)

    if #candidates == 0 then
        UIManager:show(InfoMessage:new{{ text = "Library sweep: nothing new to adopt.", timeout = 4 }})
        return
    end

    sweep_running = true
    UIManager:show(Notification:new{{
        text = string.format("TomeSync: sweeping %d closed book(s)…", #candidates),
        timeout = 3,
    }})

    local stats = {{ matched = 0, adopted = 0, unmatched = 0, failed = 0 }}
    local idx = 1
    local CHUNK = 5

    local function finish(aborted)
        sweep_running = false
        self:_saveState("tomesync_sweep", sweep)
        UIManager:show(InfoMessage:new{{
            text = string.format(
                "Library sweep %s.\\n\\nMatched in Tome: %d\\nState adopted: %d\\nNot in Tome: %d\\nFailed: %d",
                aborted and "stopped (server unreachable)" or "done",
                stats.matched, stats.adopted, stats.unmatched, stats.failed),
            timeout = 8,
        }})
    end

    local function step()
        if idx > #candidates then finish(false) return end
        local batch = {{}}
        while idx <= #candidates and #batch < CHUNK do
            table.insert(batch, candidates[idx])
            idx = idx + 1
        end
        -- Phase 2: hash + batch-resolve this chunk, then adopt each match.
        local hashes, by_hash = {{}}, {{}}
        for _, c in ipairs(batch) do
            local md5 = util.partialMD5(c.path)
            if md5 then
                table.insert(hashes, md5)
                by_hash[md5] = c
            else
                stats.failed = stats.failed + 1
            end
        end
        local ok, resp, code = pcall(apiRequest, "POST", "/tome-sync/match-hashes",
                                     {{ hashes = hashes }})
        if not ok or type(resp) ~= "table" or type(resp.matches) ~= "table"
                or (type(code) == "number" and code >= 300) then
            -- Server gone mid-run: stop; the ledger keeps everything adopted so far.
            finish(true)
            return
        end
        for _, md5 in ipairs(hashes) do
            local c = by_hash[md5]
            local book_id = resp.matches[md5]
            if type(book_id) == "number" then
                stats.matched = stats.matched + 1
                -- A sweep match is a real resolve: everything else (positions,
                -- annotations, ratings) now works for this book too.
                self.book_map[c.path] = book_id
                if self:_adoptSidecar(book_id, c.path) then
                    stats.adopted = stats.adopted + 1
                    sweep.done[c.path] = c.smtime
                else
                    stats.failed = stats.failed + 1
                end
            else
                stats.unmatched = stats.unmatched + 1
                -- Ledger it anyway — no point re-hashing every run; a changed
                -- sidecar (mtime) will retry it naturally.
                sweep.done[c.path] = c.smtime
            end
        end
        self:_saveState("tomesync_book_map", self.book_map)
        self:_saveState("tomesync_sweep", sweep)
        UIManager:scheduleIn(0.5, step)
    end
    step()
end

function TomeSync:_adoptSidecar(book_id, path)
    -- Read the CLOSED book's sidecar (no document open) and push its state;
    -- the server fills gaps only, so nothing here can clobber live sync state.
    local DocSettings = require("docsettings")
    local okd, ds = pcall(DocSettings.open, DocSettings, path)
    if not okd or not ds then return false end
    local summary = ds:readSetting("summary") or {{}}
    local pct = tonumber(ds:readSetting("percent_finished"))
    local xp = ds:readSetting("last_xpointer")
    local status = nil
    if summary.status == "complete" then
        status = "read"
    elseif summary.status == "reading" then
        status = "reading"
    end
    -- "abandoned" has no Tome equivalent; the book's other state still adopts.
    local body = {{
        status = status,
        rating = tonumber(summary.rating),
        review = (type(summary.note) == "string" and summary.note ~= "") and summary.note or nil,
        percentage = pct,
        progress = (type(xp) == "string") and xp or nil,
        device = deviceName(),
    }}
    if body.status == nil and body.rating == nil and body.percentage == nil then
        -- Bare sidecar (layout settings only): nothing to adopt, count as done.
        return true
    end
    local ok, _resp, code = pcall(apiRequest, "POST", "/tome-sync/sweep/" .. book_id, body)
    return ok and type(code) == "number" and code < 300
end

-- ── Self-update ──────────────────────────────────────────────────────────────

-- on_result(avail) where avail is a {{build, semver}} table if newer, false if
-- up to date, or nil + err message on failure.
function TomeSync:checkForUpdate(on_result)
    local ok, info, code = pcall(apiRequest, "GET", "/plugin/version")
    if not ok or not info or (type(code) == "number" and code >= 300) then
        on_result(nil, "Could not reach server.")
        return
    end
    local server_build = tonumber(info.build or info.version)
    if not server_build then
        on_result(nil, "Server did not report a build.")
        return
    end
    if server_build > BUILD then
        on_result({{ build = server_build, semver = info.semver }})
    else
        on_result(false)
    end
end

function TomeSync:installUpdate(new_build)
    local body, code = fetchText("/plugin/main-impl.lua")
    if not body then
        UIManager:show(InfoMessage:new{{
            text = "Download failed (" .. tostring(code) .. ").\\nNothing changed.",
            timeout = 5,
        }})
        return
    end
    local valid, why = validateImpl(body)
    if not valid then
        UIManager:show(InfoMessage:new{{
            text = "Update rejected: " .. tostring(why) .. ".\\nNothing changed.",
            timeout = 6,
        }})
        return
    end
    -- Back up the current (known-good) impl, then atomically swap in the new one.
    local current = readWhole(IMPL_PATH)
    if current and not writeWhole(IMPL_BAK, current) then
        UIManager:show(InfoMessage:new{{ text = "Could not write backup.", timeout = 5 }})
        return
    end
    if not writeWhole(IMPL_PATH .. ".new", body) then
        UIManager:show(InfoMessage:new{{ text = "Could not write update.", timeout = 5 }})
        return
    end
    if not os.rename(IMPL_PATH .. ".new", IMPL_PATH) then
        os.remove(IMPL_PATH .. ".new")
        UIManager:show(InfoMessage:new{{ text = "Could not install update.", timeout = 5 }})
        return
    end
    -- Arm the rollback state machine: unconfirmed until the new impl's init() runs.
    local cur_state = G_reader_settings:readSetting("tomesync_update") or {{}}
    G_reader_settings:saveSetting("tomesync_update", {{
        build      = new_build,
        confirmed  = false,
        boots      = 0,
        prev_build = cur_state.build or BUILD,
    }})
    G_reader_settings:flush()
    UIManager:show(InfoMessage:new{{
        text = "TomeSync updated to build " .. new_build .. ".\\nRestart KOReader to apply.",
        timeout = 8,
    }})
end

function TomeSync:_promptUpdate(avail)
    local ConfirmBox = require("ui/widget/confirmbox")
    UIManager:show(ConfirmBox:new{{
        text = string.format("TomeSync update available: %s (build %d).\\nInstall now?",
            avail.semver or "?", avail.build),
        ok_text = "Install",
        ok_callback = function() self:installUpdate(avail.build) end,
    }})
end

-- ── Menu ─────────────────────────────────────────────────────────────────────

-- ── Send-to-KOReader inbox (beta) ────────────────────────────────────────────

-- Poll the server inbox. Sets inbox_enabled (false on 404 = feature off),
-- inbox_items and inbox_count. Offline/transient errors keep the last state.
function TomeSync:_refreshInbox()
    local data, code = apiRequest("GET", "/tome-sync/inbox")
    if code == 404 then
        self.inbox_enabled = false
        self.inbox_count   = 0
        self.inbox_items   = {{}}
        return
    end
    if type(data) == "table" and data.items then
        self.inbox_enabled = true
        self.inbox_items   = data.items
        self.inbox_count   = data.count or #data.items
    end
end

-- Download the given inbox items (filing each by series/author via the shared
-- downloader) and mark each delivered on success. Shows one roll-up popup.
function TomeSync:_deliverInbox(items)
    local delivered, failed = 0, 0
    for _, item in ipairs(items) do
        -- item.series may be JSON null, which rapidjson decodes to a truthy
        -- userdata sentinel (not nil) — so type-check rather than compare to nil.
        local series_name = "__unserialized__"
        if type(item.series) == "string" and item.series ~= "" then
            series_name = item.series
        end
        -- Honour the file pinned at enqueue; otherwise let the downloader choose.
        local files = item.files
        if item.pinned_file_id then
            for _, f in ipairs(item.files or {{}}) do
                if f.id == item.pinned_file_id then files = {{ f }}; break end
            end
        end
        local book = {{
            id = item.book_id, title = item.title,
            series_index = item.series_index, author = item.author,
            files = files,
        }}
        local res = self:_downloadSeriesBooks(series_name, {{ book }}, nil, item.book_type, true)
        if res and res.failed == 0 then
            delivered = delivered + 1
            pcall(apiRequest, "POST", "/tome-sync/inbox/" .. item.id .. "/delivered")
        else
            failed = failed + 1
        end
    end
    pcall(function() self:_refreshInbox() end)
    UIManager:show(InfoMessage:new{{
        text = string.format("Inbox\\n\\nDelivered: %d\\nFailed: %d", delivered, failed),
        timeout = 5,
    }})
end

-- Inbox drill-down: a "Download all" row plus one row per queued book.
function TomeSync:_inboxMenu()
    whenConnected(function() self:_inboxMenuImpl() end)
end

function TomeSync:_inboxMenuImpl()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{{ text = "WiFi not connected.", timeout = 3 }})
        return
    end
    self:_refreshInbox()
    local items = self.inbox_items or {{}}
    if #items == 0 then
        UIManager:show(InfoMessage:new{{ text = "Inbox is empty.", timeout = 3 }})
        return
    end

    local menu_items = {{}}
    table.insert(menu_items, {{
        text     = string.format("Download all (%d)", #items),
        callback = function()
            if self._inbox_menu then UIManager:close(self._inbox_menu) end
            self:_deliverInbox(items)
        end,
    }})
    for _, item in ipairs(items) do
        local label
        if type(item.series_index) == "number" then
            local vol = item.series_index
            if vol == math.floor(vol) then vol = math.floor(vol) end
            label = "Vol. " .. tostring(vol) .. " — " .. item.title
        else
            label = item.title
        end
        table.insert(menu_items, {{
            text     = label,
            callback = function()
                if self._inbox_menu then UIManager:close(self._inbox_menu) end
                self:_deliverInbox({{ item }})
            end,
        }})
    end

    self._inbox_menu = Menu:new{{
        title       = "Inbox",
        item_table  = menu_items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }}
    UIManager:show(self._inbox_menu)
end

-- Edit the custom download-path template in an input dialog. Validates by
-- rendering a sample book before saving, so an unknown token is rejected here
-- rather than silently falling back to the default layout per download.
function TomeSync:_editDownloadTemplate()
    local current = G_reader_settings:readSetting("tomesync_download_template") or ""
    local dialog
    dialog = InputDialog:new{{
        title       = "Download path template",
        input       = current ~= "" and current
                      or "{{book_type}}/{{series}}/{{volume:00}} - {{title}}",
        description = "Tokens: {{book_type}} {{series}} {{volume}} {{volume:00}} "
                      .. "{{title}} {{author}} \\u{{2014}} wrap in {{Lower(...)}} or "
                      .. "{{Upper(...)}} to force case.\\n"
                      .. "\\"/\\" starts a new folder. The file extension is "
                      .. "appended automatically.\\n"
                      .. "Leave empty to restore the default layout.",
        buttons     = {{{{
            {{
                text     = "Cancel",
                id       = "close",
                callback = function() UIManager:close(dialog) end,
            }},
            {{
                text             = "Save",
                is_enter_default = true,
                callback         = function()
                    local tpl = dialog:getInputText()
                    if tpl == "" then
                        G_reader_settings:delSetting("tomesync_download_template")
                        UIManager:close(dialog)
                        return
                    end
                    local sample = renderDownloadPath(tpl, {{
                        book_type = "novels", series = "Sample Series",
                        volume = 3, title = "Sample Title", author = "Sample Author",
                    }})
                    if not sample then
                        UIManager:show(InfoMessage:new{{
                            text = "Template is invalid (unknown token or "
                                   .. "renders empty) \\u{{2014}} not saved.",
                            timeout = 4,
                        }})
                        return
                    end
                    G_reader_settings:saveSetting("tomesync_download_template", tpl)
                    UIManager:close(dialog)
                    UIManager:show(InfoMessage:new{{
                        text    = "Downloads will be saved as:\\n" .. sample .. ".epub",
                        timeout = 5,
                    }})
                end,
            }},
        }}}},
    }}
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function TomeSync:_menuItems()
    local in_book = self.ui and self.ui.document

    -- Settings submenu: persistent toggles and diagnostics, set once and
    -- forgotten — kept out of the top level so frequent actions stay reachable.
    local settings_items = {{}}
    table.insert(settings_items, {{
        text         = "Auto-connect WiFi when needed",
        help_text    = "When a TomeSync action needs the server and WiFi is down, "
                       .. "let KOReader re-establish the connection instead of "
                       .. "failing with \\"offline\\". Helps devices that "
                       .. "aggressively sleep WiFi (e.g. PocketBook).",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_connect") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_connect",
                not G_reader_settings:isTrue("tomesync_auto_connect"))
        end,
    }})
    -- Position pull strategy (like stock kosync): what to do when the server
    -- position differs from this device's on book open.
    local function pullModeItems(key, default)
        local function current()
            return G_reader_settings:readSetting(key) or default
        end
        local items = {{}}
        for _, m in ipairs({{
            {{ "prompt", "Ask before jumping" }},
            {{ "silent", "Jump automatically" }},
            {{ "never",  "Do nothing" }},
        }}) do
            local value, label = m[1], m[2]
            table.insert(items, {{
                text         = label,
                checked_func = function() return current() == value end,
                callback     = function()
                    if value == default then
                        G_reader_settings:delSetting(key)
                    else
                        G_reader_settings:saveSetting(key, value)
                    end
                end,
            }})
        end
        return items
    end
    table.insert(settings_items, {{
        text           = "Server position is ahead",
        help_text      = "What to do on book open when the server position is "
                       .. "further along than this device (you read elsewhere).",
        sub_item_table = pullModeItems("tomesync_pull_forward", "silent"),
    }})
    table.insert(settings_items, {{
        text           = "Server position is behind",
        help_text      = "What to do on book open when the server position is "
                       .. "earlier than this device (e.g. re-reading a section "
                       .. "on another device).",
        sub_item_table = pullModeItems("tomesync_pull_backward", "never"),
    }})
    local function currentTemplate()
        return G_reader_settings:readSetting("tomesync_download_template") or ""
    end
    table.insert(settings_items, {{
        text           = "Download location & naming",
        separator      = true,
        sub_item_table = {{
            {{
                text         = "Default (type and series folders)",
                help_text    = "Downloads land under the home folder as "
                               .. "book-type/series/Vol. N \\u{{2014}} Title; "
                               .. "standalones go under their author instead "
                               .. "of a series folder.",
                checked_func = function() return currentTemplate() == "" end,
                callback     = function()
                    G_reader_settings:delSetting("tomesync_download_template")
                end,
            }},
            {{
                text         = "Flat in home folder",
                help_text    = "Every download lands directly in the home "
                               .. "folder, named \\"Series - NN - Title\\". "
                               .. "Series and volume are dropped for books "
                               .. "that have none.",
                checked_func = function() return currentTemplate() == FLAT_TEMPLATE end,
                callback     = function()
                    G_reader_settings:saveSetting("tomesync_download_template", FLAT_TEMPLATE)
                end,
            }},
            {{
                text         = "Custom template",
                help_text    = "Build the path from tokens: {{book_type}} "
                               .. "{{series}} {{volume}} {{volume:00}} {{title}} "
                               .. "{{author}}, wrapped in {{Lower(...)}} or "
                               .. "{{Upper(...)}} to force case. \\"/\\" starts a "
                               .. "new folder; the file extension is appended "
                               .. "automatically.",
                checked_func = function()
                    local t = currentTemplate()
                    return t ~= "" and t ~= FLAT_TEMPLATE
                end,
                callback     = function()
                    self:_editDownloadTemplate()
                end,
            }},
        }},
    }})

    local sub_items = {{}}

    -- Always-visible items
    table.insert(sub_items, {{
        text     = "Browse series",
        callback = function() self:_browseSeriesMenu() end,
    }})
    table.insert(sub_items, {{
        text     = "Sync reading history",
        callback = function()
            whenConnected(function() self:_syncReadingStats(true) end)
        end,
    }})
    table.insert(sub_items, {{
        text      = "Sync closed books",
        help_text = "Adopt status, rating and progress from books on this "
                  .. "device that TomeSync has never synced (read before Tome, "
                  .. "sideloaded, or opened under another launcher). Only fills "
                  .. "what Tome doesn't already have.",
        callback  = function() self:_sweepLibrary() end,
    }})
    -- Inbox: only shown when the server has Send-to-KOReader enabled (set by the
    -- launch poll). Badge shows the pending count.
    if self.inbox_enabled then
        table.insert(sub_items, {{
            text_func = function() return string.format("Inbox (%d)", self.inbox_count or 0) end,
            callback  = function() self:_inboxMenu() end,
        }})
    end
    table.insert(settings_items, {{
        text     = "Test connection",
        callback = function()
            whenConnected(function()
                local ok, result, code = pcall(apiRequest, "GET", "/health")
                if ok and type(code) == "number" and code >= 200 and code < 300 then
                    UIManager:show(InfoMessage:new{{
                        text = "Connected to " .. SERVER_URL
                               .. "\\nUser: " .. USERNAME,
                        timeout = 4,
                    }})
                else
                    local err = tostring(result or "unknown error")
                    UIManager:show(InfoMessage:new{{
                        text = "Connection failed!\\n" .. SERVER_URL
                               .. "\\nError: " .. err,
                        timeout = 6,
                    }})
                end
            end)
        end,
    }})
    table.insert(settings_items, {{
        text     = "Re-resolve all books",
        separator = true,
        callback = function()
            self.book_map = {{}}
            self.book_id = nil
            self:_saveState("tomesync_book_map", {{}})
            UIManager:show(InfoMessage:new{{
                text = "All book mappings cleared.\\nRe-open a book to re-resolve.",
                timeout = 3,
            }})
        end,
    }})
    table.insert(settings_items, {{
        text     = "Check for updates",
        callback = function()
            whenConnected(function()
                self:checkForUpdate(function(avail, err)
                    if avail then
                        self:_promptUpdate(avail)
                    elseif avail == false then
                        UIManager:show(InfoMessage:new{{
                            text = "TomeSync is up to date (build " .. BUILD .. ").",
                            timeout = 4,
                        }})
                    else
                        UIManager:show(InfoMessage:new{{
                            text = err or "Update check failed.",
                            timeout = 5,
                        }})
                    end
                end)
            end)
        end,
    }})
    table.insert(settings_items, {{
        text         = "Auto-check for updates on launch",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_check") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_check",
                not G_reader_settings:isTrue("tomesync_auto_check"))
        end,
    }})
    table.insert(settings_items, {{
        text_func      = function() return "Device name: " .. deviceName() end,
        help_text      = "The label Tome shows for this device (reading log, "
                       .. "\\"Where you read\\"). Defaults to the device model; "
                       .. "set a name if you use several devices of the same "
                       .. "kind.",
        keep_menu_open = true,
        callback       = function() self:_editDeviceName() end,
    }})
    table.insert(settings_items, {{
        text         = "Auto-sync reading history on launch",
        help_text    = "Pushes KOReader's own page-level reading history to Tome so "
                       .. "your stats include reading from before TomeSync. The first "
                       .. "sync backfills everything (chunked and resumable); later "
                       .. "syncs send only new reading. Reading time and pages only - "
                       .. "never your read/unread status.",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_sync_stats") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_sync_stats",
                not G_reader_settings:isTrue("tomesync_auto_sync_stats"))
        end,
    }})
    table.insert(settings_items, {{
        text         = "Sync on suspend",
        help_text    = "When the device goes to sleep, catch up anything still "
                       .. "pending - sessions, ratings and (if enabled above) "
                       .. "reading history - so your stats are current in the "
                       .. "morning without waking the device. Only syncs when "
                       .. "WiFi is already connected; enable aggressive sync "
                       .. "below to turn WiFi on first.",
        checked_func = function() return G_reader_settings:isTrue("tomesync_sync_on_suspend") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_sync_on_suspend",
                not G_reader_settings:isTrue("tomesync_sync_on_suspend"))
        end,
    }})
    table.insert(settings_items, {{
        text         = "Aggressive sync (turn WiFi on at suspend)",
        help_text    = "If WiFi is off when the device goes to sleep, turn it on, "
                       .. "sync, and let the device power it back down as it "
                       .. "suspends. Uses more battery than plain sync on "
                       .. "suspend. If the device sleeps before the sync "
                       .. "finishes, it completes on the next connection.",
        enabled_func = function() return G_reader_settings:isTrue("tomesync_sync_on_suspend") end,
        checked_func = function() return G_reader_settings:isTrue("tomesync_suspend_wifi") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_suspend_wifi",
                not G_reader_settings:isTrue("tomesync_suspend_wifi"))
        end,
    }})
    -- Idle cap: how long a single page may count before the rest of the gap is
    -- treated as the device sitting awake unread (fell asleep, cover open).
    local function idleCapItems()
        local function current()
            local m = G_reader_settings:readSetting("tomesync_idle_cap_minutes")
            if m == nil then m = IDLE_CAP_DEFAULT_MINUTES end
            return m
        end
        local items = {{}}
        for _, entry in ipairs({{
            {{ 5,  "5 minutes" }},
            {{ 10, "10 minutes (default)" }},
            {{ 15, "15 minutes" }},
            {{ 30, "30 minutes" }},
            {{ 0,  "Off - count wall-clock time" }},
        }}) do
            local value, label = entry[1], entry[2]
            table.insert(items, {{
                text         = label,
                checked_func = function() return current() == value end,
                callback     = function()
                    if value == IDLE_CAP_DEFAULT_MINUTES then
                        G_reader_settings:delSetting("tomesync_idle_cap_minutes")
                    else
                        G_reader_settings:saveSetting("tomesync_idle_cap_minutes", value)
                    end
                end,
            }})
        end
        return items
    end
    table.insert(settings_items, {{
        text           = "Idle time cap",
        help_text      = "Longest gap between page turns that still counts as "
                       .. "reading. If the device is left awake without turning "
                       .. "pages - you fell asleep, or the cover did not put it "
                       .. "to sleep - time beyond the cap is not booked to the "
                       .. "reading session.",
        sub_item_table = idleCapItems(),
    }})

    -- In-book items
    if in_book then
        table.insert(sub_items, {{
            text     = "Download full series",
            callback = function() self:_downloadCurrentBookSeries(false) end,
        }})
        table.insert(sub_items, {{
            text     = "Download rest of series",
            callback = function() self:_downloadCurrentBookSeries(true) end,
        }})
        table.insert(sub_items, {{
            text         = "Sync now",
            callback     = function()
                whenConnected(function()
                    if self.book_id then
                        self:_syncPositionNow()
                        self:_syncAnnotations()
                        self:_pushRatingOnLeave()
                    end
                    self:_flushPendingSessions()
                    self:_flushPendingRatings()
                    local pending = #self.pending_sessions
                    local msg
                    if self.book_id then
                        local pct = self:_getCurrentPercentage()
                        msg = string.format("Synced: %.1f%%", pct * 100)
                    else
                        msg = "Book not resolved (position not synced)"
                    end
                    if pending > 0 then
                        msg = msg .. string.format("\\n%d session(s) still pending", pending)
                    end
                    UIManager:show(InfoMessage:new{{
                        text = msg,
                        timeout = 4,
                    }})
                end)
            end,
        }})
        table.insert(sub_items, {{
            text = self.enabled and "Tracking: on (tap to pause)"
                or "Tracking: paused (tap to resume)",
            help_text = "Pauses all automatic tracking and syncing — sessions, "
                        .. "position, highlights — until resumed. Resets to on "
                        .. "when KOReader restarts.",
            callback = function()
                self.enabled = not self.enabled
                UIManager:show(InfoMessage:new{{
                    text    = self.enabled and "TomeSync tracking resumed."
                        or "Tracking paused for this session.\\nTurns back on at next KOReader start.",
                    timeout = 3,
                }})
            end,
        }})
        table.insert(sub_items, {{
            separator = true,
            text_func = function()
                local n = #self.pending_sessions
                if n > 0 then
                    return string.format("Pending sessions (%d)", n)
                end
                return "Pending sessions (0)"
            end,
            callback = function()
                local n = #self.pending_sessions
                if n == 0 then
                    UIManager:show(InfoMessage:new{{
                        text = "No pending sessions.",
                        timeout = 3,
                    }})
                else
                    local lines = string.format("%d session(s) waiting to sync.\\n", n)
                    for i, s in ipairs(self.pending_sessions) do
                        if i > 5 then lines = lines .. "\\n..."; break end
                        lines = lines .. string.format("\\n%s (%s)",
                            s.started_at or "?", s.device or "?")
                    end
                    UIManager:show(InfoMessage:new{{
                        text = lines,
                        timeout = 8,
                    }})
                end
            end,
        }})
    end

    table.insert(sub_items, {{
        text           = "Settings",
        sub_item_table = settings_items,
    }})
    table.insert(sub_items, {{
        text     = "About",
        callback = function()
            UIManager:show(InfoMessage:new{{
                text    = "TomeSync " .. SEMVER .. " (build " .. BUILD .. ")"
                          .. "\\nSyncs with your Tome library.",
                timeout = 4,
            }})
        end,
    }})

    return sub_items
end

function TomeSync:addToMainMenu(menu_items)
    menu_items.tomesync = {{
        text = "TomeSync",
        -- Rebuilt each open so the Inbox badge/visibility reflects the latest
        -- poll rather than the state frozen at init().
        sub_item_table_func = function() return self:_menuItems() end,
    }}
end

-- Show the full TomeSync menu as a standalone popup (used by the "Open menu"
-- gesture). Reuses _menuItems() so it always matches the wrench-menu contents.
-- The plain Menu widget has no checkboxes or nested tables (TouchMenu-only),
-- so toggles get their state appended to the label and submenus open as
-- another popup.
function TomeSync:_openMenu()
    self:_showPopupMenu("TomeSync", self:_menuItems())
end

function TomeSync:_showPopupMenu(title, raw)
    local items = {{}}
    for _, it in ipairs(raw) do
        local orig      = it.callback
        local sub       = it.sub_item_table
        local text_func = it.text_func
        if it.checked_func and not text_func then
            local base, checked_func = it.text, it.checked_func
            text_func = function()
                return base .. (checked_func() and ": on" or ": off")
            end
        elseif sub and not text_func then
            text_func = function() return it.text .. " \\u{{25B8}}" end
        end
        table.insert(items, {{
            text      = it.text,
            text_func = text_func,
            callback  = function()
                if self._gesture_menu then UIManager:close(self._gesture_menu) end
                if sub then
                    self:_showPopupMenu(it.text, sub)
                elseif orig then
                    orig()
                end
            end,
        }})
    end
    self._gesture_menu = Menu:new{{
        title       = title,
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }}
    UIManager:show(self._gesture_menu)
end

logger.info("TomeSync: main_impl.lua loaded successfully, returning plugin class")
return TomeSync
'''
