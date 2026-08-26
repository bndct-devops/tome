"""Per-user download limits (issue #190).

``User.download_limit`` semantics:

  NULL  -> unlimited (the default; every pre-existing account)
  0     -> downloads disabled
  N > 0 -> at most N files per UTC day

Every download path — single file (``books.py``), bulk ZIP (``downloads.py``),
OPDS and TomeSync — must call :func:`enforce_download_limit` before serving and
:func:`record_download` per file served. Admins are never limited and never
counted. The deprecated ``UserPermission.can_download`` flag is intentionally
not consulted: it has never been enforced, and starting to would silently lock
out accounts whose flag was unticked back when the old permissions editor was
in use.

Days are UTC calendar days (not the stats "reading day"): quota clients
(OPDS apps, KOReader) send no timezone, so a fixed boundary is the only
consistent one.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.permissions import is_admin
from backend.models.download_event import DownloadEvent
from backend.models.user import User


def _utc_day_start() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def downloads_used_today(db: Session, user_id: int) -> int:
    return (
        db.query(func.count(DownloadEvent.id))
        .filter(
            DownloadEvent.user_id == user_id,
            DownloadEvent.created_at >= _utc_day_start(),
        )
        .scalar()
        or 0
    )


def enforce_download_limit(db: Session, user: User, count: int = 1) -> None:
    """Raise 403 if serving ``count`` more files would exceed the user's
    daily limit. Call before doing any expensive work (baking, zipping)."""
    if is_admin(user):
        return
    limit = user.download_limit
    if limit is None:
        return
    if limit <= 0:
        raise HTTPException(status_code=403, detail="Downloads are disabled for your account")
    used = downloads_used_today(db, user.id)
    if used + count > limit:
        raise HTTPException(
            status_code=403,
            detail=f"Daily download limit reached ({used}/{limit} used today)",
        )


def record_download(db: Session, user: User, book_id: int | None) -> None:
    """Count one served file toward the user's daily limit. No-op for admins
    and for users with no limit set — an unlimited account writes no rows, so
    flipping a limit on later starts counting from that moment."""
    if is_admin(user) or user.download_limit is None:
        return
    db.add(DownloadEvent(user_id=user.id, book_id=book_id))
    db.commit()
