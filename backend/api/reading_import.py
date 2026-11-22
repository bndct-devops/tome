"""Reading-history CSV import (Goodreads / StoryGraph) — preview + apply.

Stateless two-step: POST the file for a matched preview, then POST the
selected proposals to apply. Applying is FILL-GAPS-ONLY: it never overwrites
an existing status, rating, review, or finish date — an import can't clobber
live sync state or curation (same philosophy as the TomeSync library sweep).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.permissions import book_visibility_filter
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.reading_import import match_rows, parse_csv
from backend.services.audit import audit

log = logging.getLogger(__name__)
router = APIRouter(prefix="/import", tags=["import"])


@router.post("/reading-csv")
def preview_reading_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    data = file.file.read(20 * 1024 * 1024)
    try:
        dialect, rows, skipped_unread = parse_csv(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    books = (
        db.query(Book)
        .filter(Book.status == "active", book_visibility_filter(db, current_user))
        .all()
    )
    matched, unmatched = match_rows(rows, books)

    # Annotate what applying would actually do (fill-gaps rule) so the
    # preview is honest about no-ops.
    existing = {
        s.book_id: s
        for s in db.query(UserBookStatus).filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.book_id.in_([m["book_id"] for m in matched] or [0]),
        )
    }
    for m in matched:
        s = existing.get(m["book_id"])
        m["will_apply"] = {
            "status": s is None or (s.status or "unread") == "unread",
            "rating": m["rating"] is not None and (s is None or s.rating is None),
            "finished_on": m["finished_on"] is not None and (s is None or s.finished_at is None),
            "review": m["review"] is not None and (s is None or not s.review),
        }

    return {
        "dialect": dialect,
        "matched": matched,
        "unmatched": unmatched,
        "skipped_unread": skipped_unread,
    }


class ApplyItem(BaseModel):
    book_id: int
    status: str                       # read | reading
    rating: Optional[float] = None
    finished_on: Optional[str] = None  # ISO date
    review: Optional[str] = None


class ApplyRequest(BaseModel):
    items: list[ApplyItem]


@router.post("/reading-csv/apply")
def apply_reading_csv(
    body: ApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if len(body.items) > 5000:
        raise HTTPException(status_code=422, detail="Too many items")
    visible = {
        bid for (bid,) in db.query(Book.id).filter(
            Book.status == "active",
            Book.id.in_([i.book_id for i in body.items] or [0]),
            book_visibility_filter(db, current_user),
        )
    }
    applied = {"status": 0, "rating": 0, "finished_on": 0, "review": 0}
    skipped = 0
    for item in body.items:
        if item.book_id not in visible or item.status not in ("read", "reading"):
            skipped += 1
            continue
        row = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == current_user.id,
                    UserBookStatus.book_id == item.book_id)
            .first()
        )
        if row is None:
            row = UserBookStatus(user_id=current_user.id, book_id=item.book_id)
            db.add(row)
        if (row.status or "unread") == "unread":
            row.status = item.status
            if item.status == "read" and row.progress_pct is None:
                row.progress_pct = 1.0
            applied["status"] += 1
        if item.rating is not None and row.rating is None:
            row.rating = max(0.5, min(5.0, float(item.rating)))
            row.rated_at = datetime.utcnow()
            applied["rating"] += 1
        if item.finished_on and row.finished_at is None:
            try:
                row.finished_at = datetime.fromisoformat(item.finished_on)
                applied["finished_on"] += 1
            except ValueError:
                pass
        if item.review and not row.review:
            row.review = item.review[:10000]
            applied["review"] += 1
    db.commit()
    audit(db, "reading_import.applied", user_id=current_user.id, username=current_user.username,
          details={"items": len(body.items), "applied": applied, "skipped": skipped})
    return {"ok": True, "applied": applied, "skipped": skipped}


# ── CrossPoint Sync account export ───────────────────────────────────────────
# One file, two steps over the same code path (services/crosspoint_import.py):
# the preview runs the whole import and rolls it back; the import commits it.

def _crosspoint(file: UploadFile, db: Session, current_user: User, apply: bool) -> dict:
    from backend.core.permissions import has_role
    from backend.services import crosspoint_import

    if not has_role(current_user, "member"):
        raise HTTPException(status_code=403, detail="Importing reading history needs a member account")
    raw = file.file.read(crosspoint_import.MAX_BYTES + 1)
    try:
        return crosspoint_import.execute(db, current_user, raw, apply=apply)
    except crosspoint_import.Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001  the run rolled itself back
        log.exception("CrossPoint Sync import failed for user %s", current_user.id)
        raise HTTPException(
            status_code=500,
            detail=f"The import failed and nothing was written ({type(exc).__name__}). "
                   "Please send the preview report to the Tome developers.",
        )


@router.post("/crosspoint/preview")
def preview_crosspoint(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """What importing this CrossPoint Sync export would do. Writes nothing."""
    return _crosspoint(file, db, current_user, apply=False)


@router.post("/crosspoint/apply")
def apply_crosspoint(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Import this CrossPoint Sync export, all or nothing."""
    return _crosspoint(file, db, current_user, apply=True)
