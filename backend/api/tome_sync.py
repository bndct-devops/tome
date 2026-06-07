"""TomeSync API — custom KOReader plugin endpoints.

Auth: Bearer API key (not JWT) for all /api/tome-sync/ endpoints.
Plugin download: Bearer JWT for /api/plugin/koreader.
"""
import io
import logging
import zipfile
from datetime import datetime
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
from backend.core.permissions import user_can_see_book
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.book import Book, BookFile
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import Annotation, AnnotationTombstone, ApiKey, ReadingSession, TomeSyncPosition
from backend.models.send_queue import SendQueueItem

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
TOMESYNC_PLUGIN_BUILD = 16
TOMESYNC_PLUGIN_SEMVER = "1.2.3"
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


# ── Resolve endpoint ─────────────────────────────────────────────────────────

@router.get("/tome-sync/resolve")
def resolve_book(
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Match a filename to a Tome book ID.

    KOReader OPDS downloads save files as 'Author - Vol. X — Title.ext'.
    We try multiple strategies, including volume number extraction.
    """
    import re

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    # Extract volume number from filename (e.g. "Vol. 1", "Vol. 12", "v01")
    vol_match = re.search(r'[Vv]ol\.?\s*(\d+)', stem)
    vol_num = float(vol_match.group(1)) if vol_match else None

    # 1. Exact file path match in book_files
    book_file = (
        db.query(BookFile)
        .filter(BookFile.file_path.endswith("/" + filename) | (BookFile.file_path == filename))
        .first()
    )
    if book_file:
        book = db.get(Book, book_file.book_id)
        if book and book.status == "active":
            return {"book_id": book.id}

    # 2. Extract title part and match with volume
    title_part = None
    if "\u2014" in stem:  # em dash: 'Author - Vol. X — Title'
        title_part = stem.split("\u2014")[-1].strip()
    elif " - " in stem:  # regular dash fallback
        parts = stem.split(" - ", 1)
        title_part = parts[-1].strip()
        # Remove "Vol. X" prefix from title_part if present
        title_part = re.sub(r'^[Vv]ol\.?\s*\d+\s*[-—]?\s*', '', title_part).strip()

    if title_part:
        query = db.query(Book).filter(
            Book.title.ilike(f"%{title_part}%"), Book.status == "active"
        )
        if vol_num is not None:
            # Prefer exact volume match
            book = query.filter(Book.series_index == vol_num).first()
            if book:
                return {"book_id": book.id}
        # Fall back to first match if no volume info
        book = query.first()
        if book:
            return {"book_id": book.id}

    # 3. Reverse match: book title contained in filename, with volume
    books = db.query(Book).filter(Book.status == "active").all()
    for book in books:
        if book.title and book.title.lower() in stem.lower():
            if vol_num is not None and book.series_index is not None:
                if book.series_index == vol_num:
                    return {"book_id": book.id}
            else:
                return {"book_id": book.id}

    # 4. Reverse match without volume constraint (last resort)
    for book in books:
        if book.title and book.title.lower() in stem.lower():
            return {"book_id": book.id}

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

    pos = _get_position(db, user.id, book_id)
    if pos:
        pos.progress = body.progress
        pos.percentage = pct
        pos.device = body.device
        pos.updated_at = datetime.utcnow()
    else:
        pos = TomeSyncPosition(
            user_id=user.id,
            book_id=book_id,
            progress=body.progress,
            percentage=pct,
            device=body.device,
        )
        db.add(pos)

    # Keep UserBookStatus in sync.
    # Completion is sticky: a "read" book stays read/100% regardless of what the
    # device reports next (e.g. opening the book again from page 1).
    # Resume position always tracks the device (last-write-wins) — that is
    # handled above on the TomeSyncPosition row, not here.
    status_row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )
    if status_row:
        # Resume CFI tracks the device unconditionally (last-write-wins).
        if body.progress:
            status_row.cfi = body.progress
        if status_row.status == "read":
            # Sticky: leave status and progress_pct alone once finished.
            pass
        else:
            status_row.progress_pct = pct
            if status_row.status == "unread" and pct > 0:
                status_row.status = "reading"
            elif pct >= 0.99:
                status_row.status = "read"
                status_row.progress_pct = 1.0
    else:
        new_status = "read" if pct >= 0.99 else ("reading" if pct > 0 else "unread")
        db.add(UserBookStatus(
            user_id=user.id,
            book_id=book_id,
            status=new_status,
            progress_pct=1.0 if new_status == "read" else pct,
            cfi=body.progress,
        ))

    db.commit()
    return {"ok": True, "timestamp": datetime.utcnow().isoformat() + "Z"}


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

    # Keep UserBookStatus in sync — catches up when position PUTs failed
    # but queued sessions flush later.
    # Completion is sticky: once a book is "read", a later session (e.g. a
    # re-read) cannot drag it back to "reading" or lower its progress.
    if body.progress_end is not None:
        pct = body.progress_end
        status_row = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == body.book_id)
            .first()
        )
        if status_row:
            if status_row.status == "read":
                # Sticky: a later session never un-finishes a completed book.
                pass
            else:
                if pct > (status_row.progress_pct or 0):
                    status_row.progress_pct = pct
                if status_row.status == "unread" and pct > 0:
                    status_row.status = "reading"
                elif pct >= 0.99:
                    status_row.status = "read"
                    status_row.progress_pct = 1.0
        else:
            new_status = "read" if pct >= 0.99 else ("reading" if pct > 0 else "unread")
            db.add(UserBookStatus(
                user_id=user.id,
                book_id=body.book_id,
                status=new_status,
                progress_pct=1.0 if new_status == "read" else pct,
            ))

    db.commit()
    db.refresh(session)
    return {"session_id": session.id}


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

    @property
    def mtime(self) -> str:
        return self.datetime_updated or self.datetime or ""


class DeletedAnchor(PydanticBaseModel):
    anchor: str
    datetime: Optional[str] = None           # client deletion time (LWW key)


class SyncAnnotationsRequest(PydanticBaseModel):
    upserts: list[AnnotationItem] = []
    deletes: list[DeletedAnchor] = []

    # KOReader's Lua rapidjson encodes an empty table as a JSON object ({}), not an
    # array. Coerce that back to an empty list so an empty upserts/deletes is valid.
    @field_validator("upserts", "deletes", mode="before")
    @classmethod
    def _empty_obj_to_list(cls, v):
        return [] if v in (None, {}) else v


def _serialize_annotation(a: Annotation) -> dict:
    return {
        "id": a.id,
        "anchor": a.anchor,
        "anchor_end": a.anchor_end,
        "highlighted_text": a.highlighted_text,
        "note": a.note,
        "chapter": a.chapter,
        "color": a.color,
        "datetime": a.koreader_datetime,
        "datetime_updated": a.koreader_datetime_updated,
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


def _annotation_response(db: Session, user_id: int, book_id: int, **extra) -> dict:
    alive, tombs = _annotation_state(db, user_id, book_id)
    rows = sorted(alive.values(), key=lambda a: (a.koreader_datetime or "", a.id))
    return {
        "book_id": book_id,
        "annotations": [_serialize_annotation(a) for a in rows],
        "tombstones": [
            {"anchor": t.anchor, "deleted_at": t.client_deleted_at} for t in tombs.values()
        ],
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

    for item in body.upserts:
        if not item.anchor:
            continue
        tomb = tombs.get(item.anchor)
        # A re-add only wins over a delete if it's strictly newer than the delete.
        if tomb and item.mtime <= (tomb.client_deleted_at or ""):
            skipped += 1
            continue
        if tomb:
            db.delete(tomb); tombs.pop(item.anchor, None)
        row = alive.get(item.anchor)
        if row:
            if item.mtime >= row.effective_mtime:           # newer edit wins
                row.anchor_end = item.anchor_end or row.anchor_end
                row.highlighted_text = item.highlighted_text
                row.note = item.note
                row.chapter = item.chapter
                row.color = item.color
                row.koreader_datetime = item.datetime or row.koreader_datetime
                row.koreader_datetime_updated = item.mtime or row.koreader_datetime_updated
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

    for d in body.deletes:
        if not d.anchor:
            continue
        row = alive.get(d.anchor)
        # If a live edit is newer than this delete, the edit wins — keep it.
        if row and row.effective_mtime > (d.datetime or ""):
            skipped += 1
            continue
        if row:
            db.delete(row); alive.pop(d.anchor, None)
            deleted += 1
        tomb = tombs.get(d.anchor)
        if tomb:
            if (d.datetime or "") > (tomb.client_deleted_at or ""):
                tomb.client_deleted_at = d.datetime
        else:
            db.add(AnnotationTombstone(
                user_id=user.id, book_id=book_id, anchor=d.anchor,
                client_deleted_at=d.datetime,
            ))

    db.commit()
    return _annotation_response(
        db, user.id, book_id,
        applied={"created": created, "updated": updated, "deleted": deleted, "skipped": skipped},
    )


@router.get("/tome-sync/annotations/{book_id}")
def get_annotations_plugin(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Full annotation state (alive + tombstones) for this user+book — what the
    plugin pulls and merges on book open."""
    return _annotation_response(db, user.id, book_id)


# ── Series endpoints (API-key-authed, for the plugin) ────────────────────────

@router.get("/tome-sync/series")
def list_series(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """List all series for the series browser menu."""
    rows = (
        db.query(Book.series, func.count(Book.id).label("book_count"))
        .filter(Book.status == "active", Book.series.isnot(None))
        .group_by(Book.series)
        .order_by(Book.series)
        .all()
    )

    result = []
    for series_name, book_count in rows:
        first_book = (
            db.query(Book)
            .filter(Book.status == "active", Book.series == series_name)
            .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
            .first()
        )
        entry = {
            "name": series_name,
            "book_count": book_count,
            "first_book_id": first_book.id if first_book else None,
        }
        # Only include author when it's a real string. Emitting JSON null here
        # crashes the KOReader series browser, because rapidjson decodes null to
        # a (truthy) userdata sentinel that the plugin then tries to concatenate.
        if first_book and first_book.author:
            entry["author"] = first_book.author
        result.append(entry)

    # Append the unserialized bucket last, mirroring backend/api/books.py, so the
    # plugin's series browser exposes a single "No Series" entry through which
    # standalone books can be browsed and downloaded.
    unserialized_count = (
        db.query(func.count(Book.id))
        .filter(Book.status == "active", Book.series.is_(None))
        .scalar()
    )
    if unserialized_count:
        first_unserialized = (
            db.query(Book)
            .filter(Book.status == "active", Book.series.is_(None))
            .order_by(Book.id)
            .first()
        )
        result.append({
            "name": "__unserialized__",
            "book_count": unserialized_count,
            "first_book_id": first_unserialized.id if first_unserialized else None,
        })

    return result


@router.get("/tome-sync/series/{book_id}")
def get_series_books(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Given a book_id, return all books in the same series with file info."""
    book = db.get(Book, book_id)
    if not book or book.status != "active":
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
        .filter(Book.status == "active", series_filter)
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )

    # Use the first book's type as the series type
    book_type_slug = books[0].book_type.slug if books and books[0].book_type else "book"

    return {
        "series_name": series_name,
        "book_type": book_type_slug,
        "books": [
            {
                "id": b.id,
                "title": b.title,
                "series_index": b.series_index,
                "author": b.author,
                "files": [
                    {"id": f.id, "format": f.format, "file_size": f.file_size}
                    for f in b.files
                ],
            }
            for b in books
        ],
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
    serve_path = get_baked_path(book_file.book, book_file)

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
    db.delete(api_key)
    db.commit()


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
local UIManager        = require("ui/uimanager")
local Device           = require("device")
local NetworkMgr       = require("ui/network/manager")
local http             = require("socket.http")
local ltn12            = require("ltn12")
local rapidjson        = require("rapidjson")
local lfs              = require("libs/libkoreader-lfs")
local util             = require("util")
local Menu             = require("ui/widget/menu")
local Dispatcher       = require("dispatcher")
local Event            = require("ui/event")

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
http.TIMEOUT = 5

-- Track consecutive failures for backoff
local consecutive_failures = 0
local MAX_BACKOFF_FAILURES = 3

-- ── HTTP client ──────────────────────────────────────────────────────────────

local HEARTBEAT_PAGES = 50
local PLUGIN_VERSION  = "{TOMESYNC_PLUGIN_VERSION}"
local BUILD           = {TOMESYNC_PLUGIN_BUILD}      -- monotonic; the only thing compared
local SEMVER          = "{TOMESYNC_PLUGIN_SEMVER}"   -- human-facing display only

local function urlEncode(s)
    return s:gsub("([^%w%-%.%_%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
end

local function deviceName()
    local ok, name = pcall(function() return Device:getFriendlyDeviceName() end)
    return (ok and name) or "KOReader"
end

local function apiRequest(method, path, body)
    -- Skip immediately if WiFi is not connected — zero blocking
    if not NetworkMgr:isConnected() then
        return nil, "offline"
    end

    -- Skip requests if server has been unreachable repeatedly (backoff)
    if consecutive_failures >= MAX_BACKOFF_FAILURES then
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

    local ok, code = http.request({{
        url     = url,
        method  = method,
        headers = headers,
        source  = req_body and ltn12.source.string(req_body) or nil,
        sink    = ltn12.sink.table(resp_chunks),
    }})

    if not ok then
        consecutive_failures = consecutive_failures + 1
        logger.warn("TomeSync: request failed:", tostring(code),
                     "(" .. consecutive_failures .. "/" .. MAX_BACKOFF_FAILURES .. ")")
        return nil, code
    end

    -- Server reachable — reset backoff counter
    consecutive_failures = 0

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

local function downloadFile(book_id, file_id, dest_path)
    if not NetworkMgr:isConnected() then
        return false, "offline"
    end

    local url = SERVER_URL .. "/api/tome-sync/download/" .. book_id .. "/" .. file_id
    local fh = io.open(dest_path, "wb")
    if not fh then
        return false, "cannot open file for writing"
    end

    local saved_timeout = http.TIMEOUT
    http.TIMEOUT = 60

    local ok, code = http.request({{
        url     = url,
        method  = "GET",
        headers = {{
            ["Authorization"] = "Bearer " .. API_KEY,
        }},
        sink = ltn12.sink.file(fh),
    }})

    http.TIMEOUT = saved_timeout

    if not ok or (type(code) == "number" and code >= 300) then
        os.remove(dest_path)
        return false, tostring(code or "request failed")
    end

    return true
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
    local saved_timeout = http.TIMEOUT
    http.TIMEOUT = 30
    local ok, code = http.request({{
        url     = SERVER_URL .. "/api" .. path,
        method  = "GET",
        headers = {{ ["Authorization"] = "Bearer " .. API_KEY }},
        sink    = ltn12.sink.table(chunks),
    }})
    http.TIMEOUT = saved_timeout
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
    self.progress_start = nil
    self.last_progress  = nil
    self.enabled        = true
    self.book_map       = G_reader_settings:readSetting("tomesync_book_map") or {{}}
    self.pending_sessions = G_reader_settings:readSetting("tomesync_pending_sessions") or {{}}
    -- Send-to-KOReader inbox (beta): enabled only if the server reports the
    -- feature; count drives the menu badge. Populated by the launch poll below.
    self.inbox_enabled  = false
    self.inbox_count    = 0
    self.inbox_items    = {{}}
    -- Per-book annotation sync baseline: book_id -> {{ anchor -> mtime }} as of last
    -- sync. Lets a diff tell "I deleted this" from "this is new from another device".
    self.annot_baseline = G_reader_settings:readSetting("tomesync_annot_baseline") or {{}}
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
    local resp = self:_syncAnnotations()
    if resp == nil and not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{{ text = "Offline — highlights will sync later.", timeout = 3 }})
    else
        local n = (resp and resp.annotations) and #resp.annotations or 0
        UIManager:show(InfoMessage:new{{ text = "Highlights synced (" .. n .. " on this book).", timeout = 3 }})
    end
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

    self.page_count = self.page_count + 1
    if self.page_count % HEARTBEAT_PAGES == 0 then
        local pct = self:_getCurrentPercentage()
        self.last_progress = pct
        pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
            progress   = self:_getCurrentProgress(),
            percentage = pct,
            device     = deviceName(),
        }})
        -- Flush any offline sessions while we know WiFi is up
        self:_flushPendingSessions()
    end
end

function TomeSync:onSuspend()
    if not self.enabled or not self.book_id then return end

    -- Record the reading session (lid close = end of session)
    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration = self.session_start and (os.time() - self.session_start) or 0
    local dev      = deviceName()

    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = cfi, percentage = pct, device = dev,
    }})

    -- Sync highlights/notes alongside position (bidirectional merge with the server).
    pcall(function() self:_syncAnnotations() end)

    if duration > 10 then
        local session = {{
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", os.time()),
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
            G_reader_settings:saveSetting("tomesync_pending_sessions", self.pending_sessions)
            logger.info("TomeSync: session queued for retry, pending:", #self.pending_sessions)
        end
    end
end

function TomeSync:onResume()
    if not self.enabled or not self.book_id then return end

    -- Start a fresh session (lid open = new session)
    self.session_start  = os.time()
    self.page_count     = 0
    self.progress_start = self:_getCurrentPercentage()
    self.last_progress  = self.progress_start

    -- Push position on wake — catches up after offline periods
    self:_pushPosition()

    -- Flush any pending sessions from offline periods
    self:_flushPendingSessions()
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
    G_reader_settings:saveSetting("tomesync_pending_sessions", remaining)
    if #remaining == 0 then
        logger.info("TomeSync: all pending sessions flushed")
    else
        logger.info("TomeSync:", #remaining, "sessions still pending")
    end
end

function TomeSync:onCloseDocument()
    if not self.enabled or not self.book_id then return end

    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration = self.session_start and (os.time() - self.session_start) or 0
    local dev      = deviceName()

    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = cfi, percentage = pct, device = dev,
    }})

    -- Flush + merge highlights/notes before the book closes.
    pcall(function() self:_syncAnnotations() end)

    if duration > 10 then
        local uuid = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev)
        pcall(apiRequest, "POST", "/tome-sync/session", {{
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", os.time()),
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
    self.progress_start = nil
    self.last_progress  = nil
end

-- ── Helpers ──────────────────────────────────────────────────────────────────

function TomeSync:_tryResolve()
    local doc = self.ui and self.ui.document
    if not doc then return end
    local filename = doc.file:match("([^/]+)$") or doc.file
    logger.info("TomeSync: resolving filename:", filename)
    local rok, result, rcode = pcall(apiRequest, "GET",
        "/tome-sync/resolve?filename=" .. urlEncode(filename))
    if rok and result and type(rcode) == "number" and rcode == 200 and result.book_id then
        self.book_id = result.book_id
        self.book_map[doc.file] = self.book_id
        G_reader_settings:saveSetting("tomesync_book_map", self.book_map)
        logger.info("TomeSync: resolved to book_id", self.book_id)
    else
        logger.dbg("TomeSync: could not resolve", filename)
    end
end

function TomeSync:_initSession()
    logger.dbg("TomeSync: book opened, id =", self.book_id)
    self.session_start = os.time()
    self.page_count    = 0

    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. self.book_id)
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            self.progress_start = server_pct
            UIManager:show(InfoMessage:new{{
                text = string.format(
                    "TomeSync: Server at %.0f%% (device: %.0f%%).",
                    server_pct * 100, local_pct * 100
                ),
                timeout = 3,
            }})
            if pos.progress and self.ui and self.ui.rolling then
                pcall(function()
                    self.ui.rolling:onGotoXPointer(pos.progress, pos.progress)
                end)
            end
        else
            self.progress_start = local_pct
        end
    else
        self.progress_start = self:_getCurrentPercentage()
    end
    self.last_progress = self.progress_start
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
        if anchor then map[anchor] = {{ item = a, mtime = annotMtime(a) }} end
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

    for _, s in ipairs(alive or {{}}) do
        if s.anchor then
            local L = localmap[s.anchor]
            local smtime = s.datetime_updated or s.datetime or ""
            if not L then
                -- New highlight from another device: reconstruct so it renders.
                local ok = pcall(function()
                    ann:addItem({{
                        page = s.anchor, pos0 = s.anchor, pos1 = s.anchor_end or s.anchor,
                        text = s.highlighted_text, note = s.note, chapter = s.chapter,
                        color = s.color, drawer = "lighten",
                        datetime = s.datetime, datetime_updated = s.datetime_updated,
                    }})
                end)
                changed = changed or ok
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
        if L and L.mtime <= (t.deleted_at or "") then
            for i = #ann.annotations, 1, -1 do
                if ann.annotations[i] == L.item then
                    table.remove(ann.annotations, i); changed = true; break
                end
            end
        end
    end

    if changed then
        pcall(function() self.ui:handleEvent(Event:new("AnnotationsModified", {{ nb_highlights_added = 0 }})) end)
        pcall(function() UIManager:setDirty(self.ui.dialog, "full") end)
    end
end

function TomeSync:_syncAnnotations()
    -- Push local changes (diff vs baseline) and pull everyone else's, in one call.
    if not self.book_id then return nil end
    local localmap = self:_localAnnotationMap()
    if not localmap then return nil end
    local bk = tostring(self.book_id)
    local baseline = self.annot_baseline[bk] or {{}}

    local upserts, deletes = {{}}, {{}}
    for anchor, L in pairs(localmap) do
        if baseline[anchor] == nil or baseline[anchor] ~= L.mtime then
            local it = self:_annotItem(L.item)
            if it then table.insert(upserts, it) end
        end
    end
    local now = os.date("%Y-%m-%d %H:%M:%S")   -- local wall-clock, matches KOReader's
    for anchor, _ in pairs(baseline) do
        if localmap[anchor] == nil then
            table.insert(deletes, {{ anchor = anchor, datetime = now }})
        end
    end

    local resp = apiRequest("POST", "/tome-sync/annotations/" .. self.book_id .. "/sync",
                            {{ upserts = upserts, deletes = deletes }})
    if not resp then return nil end   -- offline/failed: keep baseline so we retry

    self:_applyServerState(resp.annotations, resp.tombstones)

    -- Rebuild the baseline from the post-merge local state.
    local newbase = {{}}
    local after = self:_localAnnotationMap() or {{}}
    for anchor, L in pairs(after) do newbase[anchor] = L.mtime end
    self.annot_baseline[bk] = newbase
    G_reader_settings:saveSetting("tomesync_annot_baseline", self.annot_baseline)
    return resp
end

function TomeSync:registerBookId(file_path, book_id)
    self.book_map[file_path] = book_id
    G_reader_settings:saveSetting("tomesync_book_map", self.book_map)
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

    -- Organize by book-type subfolder. A real series shares one folder; the No
    -- Series bucket files each book under its author (resolved per book below).
    local type_dir = base_dir .. "/" .. (book_type or "book")
    lfs.mkdir(type_dir)
    local series_dir
    if not is_no_series then
        series_dir = type_dir .. "/" .. util.getSafeFilename(series_name)
        lfs.mkdir(series_dir)
    end

    -- Build reverse lookup: book_id → local path (to skip already-downloaded books)
    local id_to_path = {{}}
    for path, bid in pairs(self.book_map) do
        id_to_path[bid] = path
    end

    -- Pre-compute the download queue so progress counts only real work.
    local queue = {{}}
    local skipped = 0
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
                local dest = dest_dir .. "/" .. fname
                if lfs.attributes(dest) then
                    skipped = skipped + 1
                else
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

    local downloaded, failed = 0, 0
    for i, item in ipairs(queue) do
        showProgress(string.format(
            "%s\\n\\nDownloading %d of %d\\n%s",
            batch_label, i, #queue, item.book.title
        ))
        if not item.file then
            failed = failed + 1
        else
            local ok, err = downloadFile(item.book.id, item.file.id, item.dest)
            if ok then
                downloaded = downloaded + 1
                self.book_map[item.dest] = item.book.id
            else
                logger.warn("TomeSync: download failed for", item.book.title, err)
                failed = failed + 1
            end
        end
    end
    if progress_msg then UIManager:close(progress_msg) end

    -- Persist book_map
    G_reader_settings:saveSetting("tomesync_book_map", self.book_map)

    if not quiet then
        UIManager:show(InfoMessage:new{{
            text = string.format(
                "%s\\n\\nDownloaded: %d\\nSkipped: %d\\nFailed: %d\\n\\nSaved to: %s",
                batch_label, downloaded, skipped, failed, series_dir or type_dir
            ),
            timeout = 8,
        }})
    end
    return {{ downloaded = downloaded, skipped = skipped, failed = failed }}
end

-- Drill-down list for one series (or the No Series bucket): a "Download all" row
-- plus one row per book, so a single title can be downloaded on its own.
function TomeSync:_seriesBooksMenu(data)
    local display = data.series_name
    if display == "__unserialized__" then display = "No Series" end

    local items = {{}}
    table.insert(items, {{
        text     = string.format("Download all (%d)", #data.books),
        callback = function()
            self:_downloadSeriesBooks(data.series_name, data.books, nil, data.book_type)
        end,
    }})
    for _, book in ipairs(data.books) do
        local label
        if type(book.series_index) == "number" then
            local vol = book.series_index
            if vol == math.floor(vol) then vol = math.floor(vol) end
            label = "Vol. " .. tostring(vol) .. " — " .. book.title
        else
            label = book.title
        end
        table.insert(items, {{
            text     = label,
            callback = function()
                self:_downloadSeriesBooks(data.series_name, {{ book }}, nil, data.book_type)
            end,
        }})
    end

    local menu = Menu:new{{
        title       = display,
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
        show_parent = self.ui or UIManager,
    }}
    UIManager:show(menu)
end

function TomeSync:_browseSeriesMenu()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{{
            text = "WiFi not connected.",
            timeout = 3,
        }})
        return
    end

    local ok, series_list, code = pcall(apiRequest, "GET", "/tome-sync/series")
    if not ok or not series_list or (type(code) == "number" and code >= 300) then
        UIManager:show(InfoMessage:new{{
            text = "Failed to load series list.",
            timeout = 4,
        }})
        return
    end

    local items = {{}}
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
                -- Fetch the books in this series, then drill into a per-book list
                -- so a single title can be downloaded instead of the whole series.
                local ok2, data, code2 = pcall(apiRequest, "GET",
                    "/tome-sync/series/" .. s.first_book_id)
                if ok2 and data and data.books then
                    self:_seriesBooksMenu(data)
                else
                    UIManager:show(InfoMessage:new{{
                        text = "Failed to load series books.",
                        timeout = 4,
                    }})
                end
            end,
        }})
    end

    local menu = Menu:new{{
        title = "Series Browser",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
        show_parent = self.ui or UIManager,
    }}
    UIManager:show(menu)
end

function TomeSync:_downloadCurrentBookSeries(rest_only)
    if not self.book_id then
        UIManager:show(InfoMessage:new{{
            text = "No book resolved. Open a book first.",
            timeout = 3,
        }})
        return
    end

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
        show_parent = self.ui or UIManager,
    }}
    UIManager:show(self._inbox_menu)
end

function TomeSync:_menuItems()
    local in_book = self.ui and self.ui.document

    local sub_items = {{}}

    -- Always-visible items
    table.insert(sub_items, {{
        text     = "Browse series",
        callback = function() self:_browseSeriesMenu() end,
    }})
    -- Inbox: only shown when the server has Send-to-KOReader enabled (set by the
    -- launch poll). Badge shows the pending count.
    if self.inbox_enabled then
        table.insert(sub_items, {{
            text_func = function() return string.format("Inbox (%d)", self.inbox_count or 0) end,
            callback  = function() self:_inboxMenu() end,
        }})
    end
    table.insert(sub_items, {{
        text     = "Test connection",
        callback = function()
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
        end,
    }})
    table.insert(sub_items, {{
        text     = "Re-resolve all books",
        callback = function()
            self.book_map = {{}}
            self.book_id = nil
            G_reader_settings:saveSetting("tomesync_book_map", {{}})
            UIManager:show(InfoMessage:new{{
                text = "All book mappings cleared.\\nRe-open a book to re-resolve.",
                timeout = 3,
            }})
        end,
    }})
    table.insert(sub_items, {{
        text     = "Check for updates",
        callback = function()
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
        end,
    }})
    table.insert(sub_items, {{
        text         = "Auto-check on launch",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_check") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_check",
                not G_reader_settings:isTrue("tomesync_auto_check"))
        end,
    }})
    table.insert(sub_items, {{
        text     = "About",
        separator = in_book,
        callback = function()
            UIManager:show(InfoMessage:new{{
                text    = "TomeSync " .. SEMVER .. " (build " .. BUILD .. ")"
                          .. "\\nSyncs with your Tome library.",
                timeout = 4,
            }})
        end,
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
                if self.book_id then
                    self:_pushPosition()
                    self:_syncAnnotations()
                end
                self:_flushPendingSessions()
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
            end,
        }})
        table.insert(sub_items, {{
            text = self.enabled and "Enabled (tap to disable)" or "Disabled (tap to enable)",
            callback = function()
                self.enabled = not self.enabled
                UIManager:show(InfoMessage:new{{
                    text    = "TomeSync " .. (self.enabled and "enabled" or "disabled"),
                    timeout = 2,
                }})
            end,
        }})
        table.insert(sub_items, {{
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
function TomeSync:_openMenu()
    local raw = self:_menuItems()
    local items = {{}}
    for _, it in ipairs(raw) do
        local orig = it.callback
        table.insert(items, {{
            text      = it.text,
            text_func = it.text_func,
            callback  = function()
                if self._gesture_menu then UIManager:close(self._gesture_menu) end
                if orig then orig() end
            end,
        }})
    end
    self._gesture_menu = Menu:new{{
        title       = "TomeSync",
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
        show_parent = self.ui or UIManager,
    }}
    UIManager:show(self._gesture_menu)
end

logger.info("TomeSync: main_impl.lua loaded successfully, returning plugin class")
return TomeSync
'''
