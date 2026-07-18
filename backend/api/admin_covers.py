"""Admin cover-quality audit: which books have missing or low-resolution covers.

Read-only listing; fixing goes through the existing per-book cover picker (or
the client-driven auto-fix, which reuses /books/{id}/cover-candidates +
POST /books/{id}/cover). PIL reads only image headers here, so a full-library
sweep stays cheap.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import is_admin
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/covers", tags=["admin"])

# Below this width a cover renders visibly soft on the dashboard grid. 300 is
# a deliberate floor: the standard Google Books cover is 329px wide and looks
# fine in practice — flagging it would drown the real offenders (the 98–128px
# thumbnails), which is what this audit exists to surface.
MIN_WIDTH = 300


@router.get("/audit")
def cover_audit(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")

    from PIL import Image

    flagged: list[dict] = []
    scanned = 0
    books = (
        db.query(Book)
        .filter(Book.status == "active")
        .order_by(Book.series.isnot(None), Book.series, Book.series_index, Book.title)
        .all()
    )
    for b in books:
        scanned += 1
        reason: str | None = None
        width = height = None
        if not b.cover_path:
            reason = "missing"
        else:
            path = settings.covers_dir / b.cover_path
            if not path.is_file():
                reason = "missing"
            else:
                try:
                    with Image.open(path) as im:  # lazy: header only, no pixel decode
                        width, height = im.size
                    if width < MIN_WIDTH:
                        reason = "low_res"
                except OSError:
                    reason = "unreadable"
        if reason:
            flagged.append({
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "series": b.series,
                "series_index": b.series_index,
                "reason": reason,
                "width": width,
                "height": height,
            })

    return {"scanned": scanned, "min_width": MIN_WIDTH, "books": flagged}
