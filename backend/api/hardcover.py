"""Hardcover sync API — per-user link/unlink, settings, status, manual sync.

All JWT-authed (Settings UI). The sync itself lives in
backend/services/hardcover_sync.py; these endpoints only manage the per-user
credential + opt-in state and expose reconcile status. 404 when the feature is
killed server-side so the UI hides the section entirely.
"""
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import encrypt_secret
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import hardcover_sync
from backend.services.audit import audit

logger = logging.getLogger(__name__)


def _require_enabled() -> None:
    if not settings.hardcover_sync_enabled:
        raise HTTPException(status_code=404, detail="Hardcover sync is disabled")


# Kill switch runs for every route in this router — an endpoint added later
# can't ship without it.
router = APIRouter(prefix="/hardcover", tags=["hardcover"],
                   dependencies=[Depends(_require_enabled)])


@router.get("/status")
def hardcover_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.hardcover_token:
        return {"linked": False}

    # One pass with conditional aggregates — this endpoint is polled every few
    # seconds while a sync runs, so no per-count re-scans.
    unmatched, matched, errored, last_synced = (
        db.query(
            func.coalesce(func.sum(case(
                (Book.hardcover_match_method.in_(["none", "excluded"]), 1), else_=0)), 0),
            func.coalesce(func.sum(case(
                (Book.hardcover_book_id.isnot(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case(
                (UserBookStatus.hardcover_error.isnot(None), 1), else_=0)), 0),
            func.max(UserBookStatus.hardcover_synced_at),
        )
        .select_from(UserBookStatus)
        .join(Book, UserBookStatus.book_id == Book.id)
        .filter(UserBookStatus.user_id == current_user.id, Book.status == "active")
        .one()
    )
    return {
        "linked": True,
        "username": current_user.hardcover_username,
        "token_status": current_user.hardcover_token_status,
        "sync_enabled": current_user.hardcover_sync_enabled,
        "linked_at": current_user.hardcover_linked_at.isoformat() if current_user.hardcover_linked_at else None,
        "last_synced_at": last_synced.isoformat() if last_synced else None,
        "unmatched_count": unmatched,
        "matched_count": matched,
        "error_count": errored,
        "sync_running": hardcover_sync.is_manual_sync_running(current_user.id),
    }


class LinkRequest(BaseModel):
    token: str


@router.post("/link")
async def hardcover_link(
    body: LinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate the user's personal token against Hardcover and store it
    (encrypted). Linking is the sync opt-in — sync_enabled flips on; the
    toggle below can pause it without unlinking."""
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Token must not be empty")
    # Hardcover requires the literal "Bearer " prefix in the authorization
    # header (verified against the live API); people paste both forms.
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token
    try:
        identity = await hardcover_sync.verify_token(token)
    except hardcover_sync.HardcoverAuthError:
        raise HTTPException(status_code=400, detail="Hardcover rejected this token")
    except hardcover_sync.HardcoverRateLimited:
        raise HTTPException(status_code=429, detail="Hardcover is rate limiting — try again in a minute")
    except hardcover_sync.HardcoverAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Hardcover: {exc}")

    current_user.hardcover_token = encrypt_secret(token)
    current_user.hardcover_user_id = identity["id"]
    current_user.hardcover_username = identity["username"]
    current_user.hardcover_token_status = "ok"
    current_user.hardcover_linked_at = datetime.utcnow()
    current_user.hardcover_sync_enabled = True
    db.commit()
    audit(db, "hardcover.linked", user_id=current_user.id, username=current_user.username,
          details={"hardcover_username": identity["username"]})
    # Linking is the opt-in — start the initial backfill right away instead of
    # making the user find "Sync now" or wait out the interval.
    started = hardcover_sync.start_manual_sync(current_user.id)
    return {"linked": True, "username": identity["username"], "sync_started": started}


@router.delete("/link", status_code=204)
def hardcover_unlink(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the stored token. Per-row sync snapshots stay — re-linking the
    same account resumes exactly where it left off."""
    current_user.hardcover_token = None
    current_user.hardcover_user_id = None
    current_user.hardcover_username = None
    current_user.hardcover_token_status = None
    current_user.hardcover_linked_at = None
    current_user.hardcover_sync_enabled = False
    db.commit()
    audit(db, "hardcover.unlinked", user_id=current_user.id, username=current_user.username)


class SyncSettingsRequest(BaseModel):
    sync_enabled: bool


@router.put("/settings")
def hardcover_settings(
    body: SyncSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.hardcover_token:
        raise HTTPException(status_code=400, detail="Link a Hardcover account first")
    current_user.hardcover_sync_enabled = body.sync_enabled
    db.commit()
    return {"sync_enabled": current_user.hardcover_sync_enabled}


@router.post("/sync-now")
async def hardcover_sync_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kick off one reconcile pass for this user in the background. An explicit
    click is the signal to spend requests on second chances: it un-parks rows
    that hit the failure cap AND clears failed match markers so unmatched books
    are looked up again (Hardcover's catalogue grows — a miss last month can be
    a hit today)."""
    if not current_user.hardcover_token:
        raise HTTPException(status_code=400, detail="Link a Hardcover account first")
    if current_user.hardcover_token_status != "ok":
        raise HTTPException(status_code=400, detail="Token expired — re-link your Hardcover account")

    db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.hardcover_fail_count > 0,
    ).update({"hardcover_fail_count": 0})
    # Match markers live on Book (shared): clearing them re-attempts the match
    # for everyone, which is a retry, not data loss.
    unmatched_ids = [
        bid for (bid,) in db.query(Book.id)
        .join(UserBookStatus, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == current_user.id,
            Book.hardcover_match_method == "none",
        )
    ]
    if unmatched_ids:
        db.query(Book).filter(Book.id.in_(unmatched_ids)).update(
            {"hardcover_match_method": None, "hardcover_matched_at": None},
            synchronize_session=False,
        )
    db.commit()
    hardcover_sync.reset_backoff(current_user.id)

    started = hardcover_sync.start_manual_sync(current_user.id)
    return {"started": started}


@router.get("/books")
def hardcover_books(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every sync-candidate book (my status rows) with its match state — the
    data source for the Hardcover page. States: matched | unmatched (tried,
    no result) | excluded (user opted out) | pending (not attempted yet)."""
    rows = (
        db.query(Book, UserBookStatus)
        .join(UserBookStatus, UserBookStatus.book_id == Book.id)
        .filter(UserBookStatus.user_id == current_user.id, Book.status == "active")
        .order_by(Book.series.asc().nullslast(), Book.series_index.asc().nullslast(), Book.title)
        .all()
    )
    out = []
    for b, row in rows:
        if b.hardcover_book_id is not None:
            state = "matched"
        elif b.hardcover_match_method == "excluded":
            state = "excluded"
        elif b.hardcover_match_method == "none":
            state = "unmatched"
        else:
            state = "pending"
        out.append({
            "book_id": b.id,
            "title": b.title,
            "series": b.series,
            "series_index": b.series_index,
            "author": b.author,
            "isbn": b.isbn,
            "cover_path": b.cover_path,
            "state": state,
            "slug": b.hardcover_slug,
            "method": b.hardcover_match_method,
            "pages": b.hardcover_pages,
            "status": row.status,
            "rating": row.rating,
            "progress_pct": row.progress_pct,
            "synced_at": row.hardcover_synced_at.isoformat() if row.hardcover_synced_at else None,
            "error": row.hardcover_error,
        })
    return out


async def _clear_book_match_state(db: Session, book: Book, current_user: User) -> bool:
    """Shared by rematch and manual match: best-effort delete the profile entry
    WE created for the acting user, then reset the book-level match and every
    user's push state (the match is book-level — after it changes, everyone's
    stored user_book ids point at the old record)."""
    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == current_user.id, UserBookStatus.book_id == book.id)
        .first()
    )
    removed = False
    if row and row.hardcover_user_book_id and current_user.hardcover_token_status == "ok":
        token = hardcover_sync.user_token(current_user)
        if token:
            async with httpx.AsyncClient(timeout=15) as client:
                removed = await hardcover_sync.delete_user_book(
                    client, token, row.hardcover_user_book_id)

    book.hardcover_book_id = None
    book.hardcover_edition_id = None
    book.hardcover_pages = None
    book.hardcover_slug = None
    book.hardcover_matched_at = None
    book.hardcover_match_method = None
    db.query(UserBookStatus).filter(UserBookStatus.book_id == book.id).update({
        "hardcover_user_book_id": None,
        "hardcover_read_id": None,
        "hardcover_synced_rating": None,
        "hardcover_synced_pct": None,
        "hardcover_synced_status": None,
        "hardcover_error": None,
        "hardcover_fail_count": 0,
    })
    return removed


class RematchRequest(BaseModel):
    mode: str = "retry"  # retry | exclude


@router.post("/books/{book_id}/rematch")
async def hardcover_rematch(
    book_id: int,
    body: RematchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Repair a wrong match (mode=retry) or stop syncing a book (mode=exclude).

    Best-effort deletes the entry WE created on the caller's Hardcover profile,
    clears the stored match, and — since the match is book-level — resets every
    user's push state for this book so nobody keeps writing to the old record.
    retry lets the next cycle re-match (do it after fixing metadata/ISBN);
    exclude stops matching attempts until a retry un-excludes it.
    """
    if body.mode not in ("retry", "exclude"):
        raise HTTPException(status_code=422, detail="mode must be 'retry' or 'exclude'")
    if not current_user.hardcover_token:
        raise HTTPException(status_code=400, detail="Link a Hardcover account first")
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    removed = await _clear_book_match_state(db, book, current_user)
    if body.mode == "exclude":
        book.hardcover_match_method = "excluded"
        book.hardcover_matched_at = datetime.utcnow()
    db.commit()
    if body.mode == "retry":
        hardcover_sync.nudge()
    return {"ok": True, "mode": body.mode, "removed_from_profile": removed}


@router.get("/search")
async def hardcover_search(
    q: str,
    current_user: User = Depends(get_current_user),
):
    """Raw Hardcover book search for the manual match picker (same pattern as
    the wishlist follow's series search, scoped to the user's own token)."""
    token = hardcover_sync.user_token(current_user)
    if not token or current_user.hardcover_token_status != "ok":
        raise HTTPException(status_code=400, detail="Link a Hardcover account first")
    if not q.strip():
        return []
    try:
        return await hardcover_sync.search_candidates(token, q.strip())
    except hardcover_sync.HardcoverAuthError:
        raise HTTPException(status_code=400, detail="Token expired — re-link your Hardcover account")
    except hardcover_sync.HardcoverRateLimited:
        raise HTTPException(status_code=429, detail="Hardcover is rate limiting — try again in a minute")
    except hardcover_sync.HardcoverAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Hardcover: {exc}")


class ManualMatchRequest(BaseModel):
    hardcover_book_id: int


@router.post("/books/{book_id}/match")
async def hardcover_manual_match(
    book_id: int,
    body: ManualMatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pin a user-chosen Hardcover record onto a Tome book. Clears the old
    match (removing the profile entry we created) first; method 'manual' is
    never auto-cleared, so the pick sticks until the user changes it."""
    token = hardcover_sync.user_token(current_user)
    if not token or current_user.hardcover_token_status != "ok":
        raise HTTPException(status_code=400, detail="Link a Hardcover account first")
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    await _clear_book_match_state(db, book, current_user)
    try:
        await hardcover_sync.resolve_manual_match(token, book, body.hardcover_book_id)
    except hardcover_sync.HardcoverAuthError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Token expired — re-link your Hardcover account")
    except hardcover_sync.HardcoverAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))
    db.commit()
    hardcover_sync.nudge()
    return {
        "ok": True,
        "hardcover_book_id": book.hardcover_book_id,
        "slug": book.hardcover_slug,
        "pages": book.hardcover_pages,
    }
