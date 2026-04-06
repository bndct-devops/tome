"""KOSync-compatible API. Mounted at /api/v1/."""
import time
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.kosync import KOSyncUser, KOSyncProgress, KOSyncDocumentMap, OPDSPendingLink, ReadingHistory
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus

router = APIRouter(prefix="/v1", tags=["kosync"])
logger = logging.getLogger(__name__)


# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_kosync_user(
    db: Session = Depends(get_db),
    x_auth_user: str | None = Header(None),
    x_auth_key: str | None = Header(None),
) -> KOSyncUser:
    if not x_auth_user or not x_auth_key:
        raise HTTPException(status_code=401, detail="Missing auth headers")
    user = db.query(KOSyncUser).filter(KOSyncUser.username == x_auth_user).first()
    if not user or user.userkey != x_auth_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Check Tome permission if linked
    if user.user_id:
        from backend.core.permissions import has_role
        tome_user = db.get(User, user.user_id)
        if tome_user and not has_role(tome_user, "member"):
            raise HTTPException(status_code=403, detail="KOSync access requires member role or above")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/healthcheck")
def healthcheck():
    return {"state": "OK"}


@router.post("/users/create", status_code=201)
def create_kosync_user(body: dict[str, Any], db: Session = Depends(get_db)):
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()  # already MD5-hashed by KOReader

    if not username or not password:
        raise HTTPException(status_code=400, detail="Invalid fields")

    existing = db.query(KOSyncUser).filter(KOSyncUser.username == username).first()
    if existing:
        raise HTTPException(status_code=402, detail="User already exists")

    # Auto-link to Tome user with the same username if one exists
    tome_user = db.query(User).filter(User.username == username).first()

    kosync_user = KOSyncUser(
        username=username,
        userkey=password,
        user_id=tome_user.id if tome_user else None,
    )
    db.add(kosync_user)
    db.commit()

    return {"username": username}


@router.get("/users/auth")
def auth_kosync_user(user: KOSyncUser = Depends(_get_kosync_user)):
    return {"authorized": "OK"}


@router.put("/syncs/progress")
def update_progress(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: KOSyncUser = Depends(_get_kosync_user),
):
    document = str(body.get("document", "")).strip()
    progress = body.get("progress")
    percentage = body.get("percentage")
    device = body.get("device")
    device_id = body.get("device_id")

    if not document or progress is None or percentage is None or not device:
        raise HTTPException(status_code=400, detail="Invalid fields")

    timestamp = int(time.time())

    existing = db.query(KOSyncProgress).filter(
        KOSyncProgress.user_id == user.id,
        KOSyncProgress.document == document,
    ).first()

    if existing:
        existing.progress = str(progress)
        existing.percentage = float(percentage)
        existing.device = device
        existing.device_id = device_id
        existing.timestamp = timestamp
    else:
        db.add(KOSyncProgress(
            user_id=user.id,
            document=document,
            progress=str(progress),
            percentage=float(percentage),
            device=device,
            device_id=device_id,
            timestamp=timestamp,
        ))

    db.commit()

    # Append to reading history for stats (book_id filled in after map lookup below)
    history_entry = ReadingHistory(
        user_id=user.user_id or 0,
        book_id=None,
        document=document,
        percentage=float(percentage),
        device=device,
    ) if user.user_id else None

    # Cross-reference to Tome book via document map and update UserBookStatus
    if user.user_id:
        doc_map = db.query(KOSyncDocumentMap).filter(
            KOSyncDocumentMap.tome_user_id == user.user_id,
            KOSyncDocumentMap.document == document,
        ).first()

        # If not mapped yet, try to auto-link to the most recent pending OPDS download
        if not doc_map:
            pending = (
                db.query(OPDSPendingLink)
                .filter(OPDSPendingLink.user_id == user.user_id)
                .order_by(OPDSPendingLink.created_at.asc())
                .first()
            )
            if pending:
                doc_map = KOSyncDocumentMap(
                    tome_user_id=user.user_id,
                    document=document,
                    book_id=pending.book_id,
                )
                db.add(doc_map)
                db.delete(pending)
                db.commit()

        if doc_map:
            pct = float(percentage)
            new_status = "read" if pct >= 0.95 else "reading"
            ubs = db.query(UserBookStatus).filter(
                UserBookStatus.user_id == user.user_id,
                UserBookStatus.book_id == doc_map.book_id,
            ).first()
            if ubs:
                ubs.progress_pct = pct
                ubs.status = new_status
            else:
                db.add(UserBookStatus(
                    user_id=user.user_id,
                    book_id=doc_map.book_id,
                    status=new_status,
                    progress_pct=pct,
                ))
            if history_entry:
                history_entry.book_id = doc_map.book_id
            db.commit()

        if history_entry:
            db.add(history_entry)
            db.commit()

    return {"document": document, "timestamp": timestamp}


@router.get("/syncs/progress/{document}")
def get_progress(
    document: str,
    db: Session = Depends(get_db),
    user: KOSyncUser = Depends(_get_kosync_user),
):
    entry = db.query(KOSyncProgress).filter(
        KOSyncProgress.user_id == user.id,
        KOSyncProgress.document == document,
    ).first()

    if not entry:
        return {}

    return {
        "document": entry.document,
        "percentage": entry.percentage,
        "progress": entry.progress,
        "device": entry.device,
        "device_id": entry.device_id,
        "timestamp": entry.timestamp,
    }
