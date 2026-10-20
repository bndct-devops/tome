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
from functools import lru_cache
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
from backend.services.book_progress import (
    apply_progress_to_status, upsert_position, restart_reading, reset_hardcover_read_state,
)
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
# BUILD 41: device-side connection settings (#181, #185) — baked server URL /
# API key / username become DEFAULT_* fallbacks overridable in Settings. The
# server URL is user-editable (a device whose baked URL went stale cannot
# self-update its way out, so the fix must live on the device); the API key is
# never typed — "Sign in with code" runs the existing Quick Connect flow
# (initiate → code entered in web UI → poll → JWT → mint plugin API key); the
# username is display-only, derived from /auth/me at pairing time. Plus an
# input_hint fix (ghost text never showed; the field was misnamed "hint") and
# a settings-menu reorder. Co-developed with @pabsan-0.
# BUILD 46: sync code — reading done offline handed to an online phone as a
# QR code (sessions, positions, ratings that could not be sent), scanned by
# the Tome app or the web UI and posted to /api/sync-code. Positions that
# fail to PUT are now remembered per book (tomesync_pending_positions) so
# the code has them; closing a book while offline now queues its session
# like the suspend path already did (it used to be dropped); the session
# queue cap rises 50 -> 200. "Scanned" moves a watermark
# (tomesync_scan_mark); the queues themselves still flush over WiFi and the
# server dedups. Gesture: "TomeSync: Show sync code".
# BUILD 47: sync code review fixes. "Show sync code" now ends the sitting
# in progress (as a lid close would) and starts a new one, so the book being
# read is in the code and not only its position; the menu count "(N)" counts
# unsent items only (the open book's live position no longer makes it read
# "(1)" after a full sync).
# BUILD 49 (48 is taken by an unmerged branch already on devices): network
# resilience (issue #249). Every request runs on KOReader's UI thread, so an
# online-but-unreachable server froze the screen for the connect timeout.
# The back-off now starts after the FIRST request with no answer (was three)
# and doubles 1, 2, 4, 8 min up to 15 (was a flat 60 s); interactive actions
# clear it. Opening a book no longer waits on the network (resolve and
# position pull run once the book is on screen). Optional fallback server
# URL (tomesync_fallback_url) tried when the main one does not answer; the
# address that answered last is tried first (tomesync_active_url).
# BUILD 50: device inventory. The plugin sends the Tome ids of the books on
# the device (book map, files that still exist) to PUT
# /tome-sync/device-books on launch, resume, WiFi up, Sync now and a few
# seconds after the book map changes; the server replaces the device's list,
# so deletes drop out on the next push. Powers "On <device>" in web and app.
# BUILD 51 / 52: released on main (single-book download crash, #259; a fresh
# pairing written to disk at once, #262).
# BUILD 53: reader link (device info, file tree, file commands) and inbox
# auto-pickup, against a server that has those routes (Tome Reader on iOS).
# It polls while the server asks for it (poll_seconds on device-info /
# commands), sends the file tree only when the server lacks its hash
# (GET /tome-sync/files/status), and never runs a command id twice.
# A Tome without the routes still costs one PUT device-info (405) per process.
# BUILD 54: the plugin's Lua became files (backend/tomesync.koplugin) and the
# connection moved out of main_impl.lua into config.lua, which self-update
# never replaces. A build-53-or-older install updating to 54 has no config.lua:
# the new impl reads the baked values back from main_impl.lua.bak (kept by the
# old updater) and writes config.lua; failing that, its next update fetches
# GET /plugin/config.lua. Bumped so those installs are offered the update.
# BUILD 55: reader link. A persistent device_id (device-info, Quick Connect
# initiate), the sign-in dialog says to approve on the phone when the server
# answers initiate with server_kind "tome-reader", and config.lua may list
# LINK_HOSTS (the phone's addresses), tried with the pinned certificate when
# the configured server does not answer. Against Tome nothing changes.
# BUILD 57: an https server pinned at a trust prompt whose new certificate is
# CA-valid for its name is unpinned instead of asking (a CA renewal); config.lua
# is text only and a name set twice makes it invalid; reader link: a heartbeat
# every 60 s for 30 min after a poll got no answer, names ending in a dot or
# space refused, no tree sent when the walk fails.
# BUILD 58: reader link: a light poll whose commands answer says inbox: N > 0
# delivers right away in the file manager (in the reader after the book closes);
# the heartbeat pauses while a book is open; a 401 "Device id mismatch" /
# "unknown key" from the link stops the poll and says to pair again.
# BUILD 59: reader link: KOReader settings report (PUT /tome-sync/reader-settings:
# capabilities, a curated settings list, fonts, profiles) and the set_setting /
# run_profile commands.
# BUILD 60: reader link: plugins report (PUT /tome-sync/plugins: every *.koplugin
# as KOReader's PluginLoader sees it, enabled from plugins_disabled) and the
# plugin_enable / plugin_install (a zip from GET /tome-sync/plugins/{id}/download)
# / plugin_remove / self_update commands.
TOMESYNC_PLUGIN_BUILD = 60
TOMESYNC_PLUGIN_SEMVER = "1.19.2"
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
    from backend.services import device_inventory
    from backend.services.ko_stats_import import rename_device
    # The inventory moves in the same transaction (rename_device commits).
    inventory_moved = device_inventory.rename(
        db, user, from_device=body.from_device, to_device=body.to_device)
    result = rename_device(db, user, from_device=body.from_device, to_device=body.to_device)
    result["inventory_moved"] = inventory_moved
    if result["rows_relabelled"] or result["watermark_moved"] or inventory_moved:
        audit(db, "ko_stats.device_renamed", user_id=user.id, username=user.username,
              resource_type="ko_page_stats", resource_id=user.id,
              details={"from": body.from_device, "to": body.to_device, **result})
    return result


class DeviceBooksRequest(PydanticBaseModel):
    device: str
    book_ids: list[int] = []
    plugin_build: Optional[int] = None

    # KOReader's Lua rapidjson encodes an empty table as a JSON object.
    @field_validator("book_ids", mode="before")
    @classmethod
    def _empty_obj_to_list(cls, v):
        return [] if v in (None, {}) else v


@router.put("/tome-sync/device-books")
def put_device_books(
    body: DeviceBooksRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """The complete list of Tome books on the calling device (plugin build 50+).
    Replaces what was stored for the device, so books deleted on the device
    drop out with the next sync. Powers the "On <device>" pills and filters."""
    from backend.services import device_inventory
    device = device_inventory.clean_device(body.device)
    if not device:
        raise HTTPException(422, "device is required")
    if len(body.book_ids) > device_inventory.MAX_BOOK_IDS:
        raise HTTPException(413, "too many book ids")
    return device_inventory.replace(
        db, user, device=device, book_ids=body.book_ids, plugin_build=body.plugin_build)


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


def _book_entry(b: Book, status_map: dict[int, str], default_type: str = "book") -> dict:
    """The book shape the plugin's volume-list menus consume. `series` and
    `status` are additive (build 33+); older plugins ignore them. A book with
    no type reports `default_type`, never null: plugins up to build 50 crash
    filing a single-book download on null (issue #259)."""
    entry = {
        "id": b.id,
        "title": b.title,
        "series_index": b.series_index,
        "author": b.author,
        "book_type": b.book_type.slug if b.book_type else default_type,
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
    # Same transition rules as the web status endpoint (users.py): stamp the
    # finish date on the way into "read" (stats and the Hardcover mirror both
    # read it), clear it on the way out, and a finished book set back to
    # "reading" starts over from page one.
    was_read = row.status == "read"
    if body.status == "read" and not was_read:
        row.finished_at = datetime.utcnow()
        hardcover_nudge()
    elif body.status != "read":
        row.finished_at = None
    if was_read and body.status != "read":
        reset_hardcover_read_state(row)
    if was_read and body.status == "reading":
        restart_reading(db, row)
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


class MetadataSyncItem(PydanticBaseModel):
    book_id: int
    ko_md5: str
    fingerprint: Optional[str] = None


class MetadataSyncRequest(PydanticBaseModel):
    items: list[MetadataSyncItem] = []

    # KOReader's Lua rapidjson encodes an empty table as a JSON object.
    @field_validator("items", mode="before")
    @classmethod
    def _coerce_empty_object(cls, v):
        return [] if isinstance(v, dict) and not v else v


METADATA_SYNC_MAX_ITEMS = 100


@router.post("/tome-sync/metadata")
def device_metadata_sync(
    body: MetadataSyncRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Metadata for books the plugin holds on the device (Tome -> KOReader).

    Each item names a book id the plugin believes a device file is, plus the
    file's KOReader partial-MD5. The pair is **only answered when Tome has
    that exact hash on record for that exact book** (recorded when the file
    was scanned or served) - a filename-heuristic resolve, a different
    edition, or a file that never passed through Tome is rejected, so the
    plugin can never write one book's metadata onto another. Books the user
    cannot see are rejected the same way (indistinguishable by design).

    ``fingerprint`` is what the plugin stored from the last answer; a match
    means "nothing changed" and the book is listed under ``unchanged``
    instead of being re-sent. See services/device_metadata.py.
    """
    from backend.models.ko_stats import KoHash
    from backend.services.device_metadata import device_metadata, metadata_fingerprint

    items = body.items[:METADATA_SYNC_MAX_ITEMS]
    ids = {i.book_id for i in items}
    if not ids:
        return {"books": [], "unchanged": [], "rejected": []}

    books = {
        b.id: b
        for b in db.query(Book)
        .options(joinedload(Book.tags))
        .filter(Book.id.in_(ids), Book.status == "active", book_visibility_filter(db, user))
        .all()
    }
    known = {
        (bid, md5)
        for bid, md5 in db.query(KoHash.book_id, KoHash.ko_partial_md5)
        .filter(KoHash.book_id.in_(ids))
        .all()
    }

    # Results are keyed by the (book_id, ko_md5) pair, not the bare id: two
    # device files can legitimately map to one book (a raw and a baked copy),
    # and the plugin must be able to tell which of them was answered.
    out: list[dict] = []
    unchanged: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        pair = {"book_id": item.book_id, "ko_md5": item.ko_md5}
        book = books.get(item.book_id)
        if book is None or (item.book_id, item.ko_md5) not in known:
            rejected.append(pair)
            continue
        if item.fingerprint and item.fingerprint == metadata_fingerprint(book):
            unchanged.append(pair)
            continue
        out.append({**pair, **device_metadata(book)})
    return {"books": out, "unchanged": unchanged, "rejected": rejected}


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
    # An untyped volume of a real series files with its siblings; an untyped
    # standalone in the bucket files under "book", not the first book's type.
    untyped = book_type_slug if book.series else "book"

    smap = _status_map(db, user.id, [b.id for b in books])
    return {
        "series_name": series_name,
        "book_type": book_type_slug,
        "books": [_book_entry(b, smap, untyped) for b in books],
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

    from backend.services.download_quota import enforce_download_limit, record_download
    enforce_download_limit(db, user)

    file_path = Path(book_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    from backend.services.metadata_embed import get_baked_path
    from backend.services.ko_hash import record_served_artifact
    serve_path = get_baked_path(book_file.book, book_file)
    record_served_artifact(db, book_file.book_id, book_file, serve_path)
    record_download(db, user, book_file.book_id)

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
    #   main_impl.lua  the real plugin, the same for everyone; the only file
    #                  updates replace
    #   config.lua     this download's connection (server URL, key, username)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tomesync.koplugin/_meta.lua", _meta_lua())
        zf.writestr("tomesync.koplugin/main.lua", _main_shim_lua())
        zf.writestr("tomesync.koplugin/main_impl.lua", _main_impl_lua())
        zf.writestr("tomesync.koplugin/config.lua",
                    _config_lua(server_url, api_key_value, current_user.username))
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
    """Serve the current main_impl.lua for self-update. Authenticated by the
    plugin's own API key. The impl carries no config (build 54+: that lives in
    the device's config.lua, which updates never touch). The shim (main.lua) is
    frozen and never served here. ``server_url`` is accepted and ignored, as
    older plugins may send it."""
    return StreamingResponse(
        io.BytesIO(_main_impl_lua().encode()),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/plugin/config.lua")
def download_config(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_api_key_user),
    server_url: str | None = None,
):
    """The caller's config.lua, for an install that has none (one that came up
    from build 53 or older). Authenticated like main-impl.lua; the key in it is
    the one the plugin authenticated with, recovered from the request header
    (plaintext keys are never stored)."""
    auth = request.headers.get("authorization", "")
    api_key_value = auth.removeprefix("Bearer ").strip()
    server_url = _baked_server_url(request, server_url)
    return StreamingResponse(
        io.BytesIO(_config_lua(server_url, api_key_value, current_user.username).encode()),
        media_type="text/plain; charset=utf-8",
    )


# ── Lua plugin source ─────────────────────────────────────────────────────────

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "tomesync.koplugin"


@lru_cache(maxsize=None)
def _plugin_file(name: str) -> str:
    """One file of the plugin as shipped (backend/tomesync.koplugin), cached."""
    return (_PLUGIN_DIR / name).read_text(encoding="utf-8")


def _meta_lua() -> str:
    return _plugin_file("_meta.lua")


def _main_shim_lua() -> str:
    # Frozen stable shim. Deployed once, never replaced by self-update. No config,
    # no network. Its only jobs: find its own dir, run the anti-brick rollback
    # state machine, dofile main_impl.lua, and return the plugin class (or a valid
    # inert stub if even the backup can't load). Keep this minimal so it never
    # needs to change — if it ever must, that's a manual redeploy.
    return _plugin_file("main.lua")


def _main_impl_lua(server_url: str = "", api_key: str = "", username: str = "",
                   auto_deliver: bool = False, server_fingerprint: str = "") -> str:
    """The plugin implementation, identical for every caller.

    Build 54 moved the connection out of the impl into config.lua (see
    ``_config_lua``); the arguments are kept so existing callers keep working
    and are ignored. Self-update replaces this file only, so a device's
    config.lua survives every update.
    """
    return _plugin_file("main_impl.lua")


def _lua_str(value: str) -> str:
    """A Lua double-quoted string literal for ``value``.

    Escapes backslash, quote and every control byte (newline included), so
    whatever the value holds it stays one string on one line."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _config_lua(server_url: str, api_key: str, username: str,
                auto_deliver: bool = False, server_fingerprint: str = "",
                link_hosts: tuple = ()) -> str:
    """The config.lua a plugin starts with (same format the plugin writes).

    auto_deliver: inbox auto-pickup on (the reader link bakes it; a plugin
    downloaded from Tome keeps it off unless the user turns it on).
    server_fingerprint: SHA-256 of the server's TLS certificate (any of
    AB:CD.., abcd..), pinned for https URLs; the reader link bakes its own.
    Empty for every Tome download: TLS behaves exactly as before.
    link_hosts: the reader link's phone addresses ("https://host:port"); a
    LINK_HOSTS line only when there are any, so a Tome download is unchanged.
    """
    server_fingerprint = "".join(c for c in server_fingerprint if c in "0123456789abcdefABCDEF:")
    hosts = ("LINK_HOSTS = { " + ", ".join(_lua_str(h) for h in link_hosts) + " }\n") if link_hosts else ""
    return (
        "-- TomeSync connection. Self-update never replaces this file.\n"
        f"SERVER_URL = {_lua_str(server_url)}\n"
        f"API_KEY = {_lua_str(api_key)}\n"
        f"USERNAME = {_lua_str(username)}\n"
        f"AUTO_DELIVER = {'true' if auto_deliver else 'false'}\n"
        f"SERVER_FINGERPRINT = {_lua_str(server_fingerprint)}\n"
        + hosts
    )
