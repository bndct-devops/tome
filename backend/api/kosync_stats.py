"""Reading stats from KOSync readers (experimental). See services/kosync_stats.py.

The path is not a typo. A reader is set up with Tome's KOSync URL, which ends
in ``/api/v1``, and CrossInk appends ``/api/v1/stats/...`` to whatever server
URL it has. So the requests arrive at ``/api/v1/api/v1/stats/...``.

The reader decides whether a server speaks this API by calling
``GET .../stats/summary`` when the user authenticates: a JSON object means
yes, a 404 means no (and it then keeps its stats to itself). Every route
answers 404 for a KOSync account that is not linked to a Tome user, because a
session needs an owner and an acknowledged upload is deleted from the device.

Replies carry exactly the fields the firmware checks before it treats an
upload as delivered (``until`` on all of them, plus ``accepted_daily``,
``accepted_session_ids`` or ``accepted``).
"""
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.kosync import _find_or_hash_link, _get_kosync_user
from backend.core.database import get_db
from backend.models.kosync import KOSyncUser
from backend.services import kosync_stats as stats

router = APIRouter(prefix="/v1/api/v1", tags=["kosync-stats"])


def _tome_user_id(user: KOSyncUser = Depends(_get_kosync_user)) -> int:
    if not user.user_id:
        raise HTTPException(
            status_code=404,
            detail="Reading stats need a KOSync account that is linked to a Tome user",
        )
    return user.user_id


def _reject(exc: stats.Rejected) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _store(db: Session, write):
    """Run a storing call. A reader that timed out retries while its first
    request may still be running; both then try to create the same row and one
    hits the unique constraint. Running it again finds the row and extends it.
    """
    try:
        return write()
    except IntegrityError:
        db.rollback()
        return write()


@router.get("/stats/summary")
def get_summary(db: Session = Depends(get_db), user_id: int = Depends(_tome_user_id)):
    return stats.summary(db, user_id)


@router.put("/stats/global")
def put_global(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user_id: int = Depends(_tome_user_id),
):
    try:
        accepted_daily = _store(db, lambda: stats.store_global(db, user_id, body))
    except stats.Rejected as exc:
        raise _reject(exc)
    return {"until": int(time.time()), "accepted_daily": accepted_daily}


@router.put("/stats/sessions")
def put_sessions(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user_id: int = Depends(_tome_user_id),
):
    def resolve_book(document: str) -> Optional[int]:
        doc_map = _find_or_hash_link(db, user_id, document)
        return doc_map.book_id if doc_map else None

    now = int(time.time())
    try:
        accepted = _store(db, lambda: stats.store_sessions(db, user_id, body, resolve_book, now))
    except stats.Rejected as exc:
        raise _reject(exc)
    return {"until": now, "accepted_session_ids": accepted}


@router.put("/stats/books")
def put_books(body: dict[str, Any], user_id: int = Depends(_tome_user_id)):
    try:
        accepted = stats.check_books(body)
    except stats.Rejected as exc:
        raise _reject(exc)
    # accepted_daily is 0 on purpose: per-book daily seconds are not stored,
    # so a reader that sends them must not mark them as delivered.
    return {"until": int(time.time()), "accepted": accepted, "accepted_daily": 0}


@router.api_route("/clippings/{document}", methods=["GET", "PUT"], status_code=501)
def clippings_not_implemented(document: str, user_id: int = Depends(_tome_user_id)):
    """Tome does not take clippings, and says so with a 501 rather than a 404.

    A reader that has Clippings switched on sends them through the same client
    as its stats, and that client reads a 404 on any upload as "this server has
    no extended API": it would stop sending stats too, until the user
    authenticates again. A 501 fails the clippings step and leaves stats alone.
    """
    raise HTTPException(status_code=501, detail="Tome does not sync clippings")
