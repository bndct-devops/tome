"""Per-user download limits (issue #190, feature B).

A single place the single-file, bulk-zip and OPDS download endpoints all call to
enforce ``UserPermission.can_download`` and ``UserPermission.download_limit``:

  can_download = False  → downloads disabled
  download_limit = 0    → downloads disabled
  download_limit = N>0  → at most N downloads per UTC day
  download_limit = NULL → unlimited (the default)

Admins are always unlimited and their downloads are not counted.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.permissions import is_admin
from backend.models.download_event import DownloadEvent


def _limit_and_perms(user):
    perms = getattr(user, "permissions", None)
    return perms, (getattr(perms, "download_limit", None) if perms else None)


def _day_start() -> datetime:
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def used_today(db: Session, user_id: int) -> int:
    return (
        db.query(DownloadEvent)
        .filter(DownloadEvent.user_id == user_id, DownloadEvent.created_at >= _day_start())
        .count()
    )


def enforce_download(db: Session, user, count: int = 1) -> None:
    """Raise 403 if `user` may not download `count` more files right now."""
    if is_admin(user):
        return
    perms, limit = _limit_and_perms(user)
    if perms is not None and perms.can_download is False:
        raise HTTPException(status_code=403, detail="Downloads are disabled for your account")
    if limit is None:
        return
    if limit <= 0:
        raise HTTPException(status_code=403, detail="Downloads are disabled for your account")
    if used_today(db, user.id) + count > limit:
        raise HTTPException(
            status_code=403,
            detail=f"Daily download limit reached ({limit} per day)",
        )


def record_download(db: Session, user, book_id: int | None) -> None:
    """Log a served download so it counts toward the daily limit. No-op for admins."""
    if is_admin(user):
        return
    db.add(DownloadEvent(user_id=user.id, book_id=book_id, created_at=datetime.utcnow()))
