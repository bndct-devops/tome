"""Admin word-count backfill endpoints.

A single cancellable background job parses every EPUB whose ``word_count`` is
still NULL and stores it. The UI polls ``/admin/word-count/status``; see
``services/word_count_job.py``. New uploads get their count at ingest, so this
only exists to backfill books that predate the feature.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.permissions import require_role
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services import word_count_job
from backend.services.audit import audit

router = APIRouter()


@router.get("/admin/word-count/status")
def word_count_status(current_user: User = Depends(get_current_user)) -> dict:
    require_role(current_user, "admin")
    return word_count_job.get_status()


@router.get("/admin/word-count/preflight")
def word_count_preflight(current_user: User = Depends(get_current_user)) -> dict:
    require_role(current_user, "admin")
    return word_count_job.preflight()


@router.post("/admin/word-count/start")
def word_count_start(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_role(current_user, "admin")
    try:
        state = word_count_job.start(username=current_user.username)
    except word_count_job.WordCountAlreadyRunning:
        raise HTTPException(409, "A word-count backfill is already running")
    audit(
        db,
        "books.word_count_backfill_started",
        user_id=current_user.id,
        username=current_user.username,
        details={"total_files": state.get("total_files"), "total_bytes": state.get("total_bytes")},
    )
    return state


@router.post("/admin/word-count/cancel")
def word_count_cancel(current_user: User = Depends(get_current_user)) -> dict:
    require_role(current_user, "admin")
    cancelled = word_count_job.request_cancel()
    return {"cancelling": cancelled, **word_count_job.get_status()}


@router.post("/admin/word-count/dismiss")
def word_count_dismiss(current_user: User = Depends(get_current_user)) -> dict:
    require_role(current_user, "admin")
    return word_count_job.dismiss()
