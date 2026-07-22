"""KOSync-compatible API. Mounted at /api/v1/.

Serves stock-KOReader kosync clients and third-party implementations
(e.g. Readest) — the sync path for people who don't run the Tome plugin.

Bridge design (issue #156): positions flow ONE WAY, Tome → KOSync. The GET
endpoint serves the web/plugin position when it is newer than the last KOSync
push, so a KOSync client opens at (roughly) the right spot. The PUT endpoint
never writes ``TomeSyncPosition`` — third-party pushes must not move
positions on devices running the Tome plugin, feed PositionHistory, or enter
the plugin's conflict handling. KOSync pushes surface in Tome as progress %
and read status only.
"""
import calendar
import time
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.kosync import KOSyncUser, KOSyncProgress, KOSyncDocumentMap, OPDSPendingLink, ReadingHistory
from backend.models.user import User

router = APIRouter(prefix="/v1", tags=["kosync"])
logger = logging.getLogger(__name__)


def _epoch(dt: datetime) -> int:
    """Naive-UTC datetime (the DB convention) → epoch seconds."""
    return calendar.timegm(dt.utctimetuple())


def _find_or_hash_link(db: Session, tome_user_id: int, document: str) -> Optional[KOSyncDocumentMap]:
    """Resolve a KOSync document hash to this user's document map.

    Existing map wins. Otherwise try a deterministic link: the KOSync
    ``document`` field is KOReader's partial-MD5 of the device file (Readest
    computes the same hash — confirmed in #156), and ``ko_hashes`` records
    that hash for every artifact Tome scanned or served. A hit is exact
    identity, so the map is created and persisted on the spot — this replaces
    guesswork for any file that came from Tome. Only active, visible books
    are linked. Returns None when the file never passed through Tome (the
    caller may fall back to the legacy OPDS heuristic, or a manual link).
    """
    doc_map = (
        db.query(KOSyncDocumentMap)
        .filter(
            KOSyncDocumentMap.tome_user_id == tome_user_id,
            KOSyncDocumentMap.document == document,
        )
        .first()
    )
    if doc_map:
        return doc_map

    from backend.core.permissions import user_can_see_book
    from backend.models.book import Book
    from backend.services.ko_hash import lookup_book_ids

    hit = lookup_book_ids(db, [document]).get(document)
    if hit is None:
        return None
    book = db.get(Book, hit)
    tome_user = db.get(User, tome_user_id)
    if not book or book.status != "active" or not tome_user:
        return None
    if not user_can_see_book(db, tome_user, book):
        return None

    doc_map = KOSyncDocumentMap(tome_user_id=tome_user_id, document=document, book_id=hit)
    db.add(doc_map)
    db.commit()
    logger.info("kosync: auto-linked document %s to book %s via ko-hash", document, hit)
    return doc_map


# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_kosync_user(
    db: Session = Depends(get_db),
    x_auth_user: str | None = Header(None),
    x_auth_key: str | None = Header(None),
) -> KOSyncUser:
    import hmac
    if not x_auth_user or not x_auth_key:
        raise HTTPException(status_code=401, detail="Missing auth headers")
    user = db.query(KOSyncUser).filter(KOSyncUser.username == x_auth_user).first()
    # Constant-time compare to prevent timing-side-channel discovery of the userkey.
    # (The userkey is an MD5 supplied by KOReader's protocol; we still want timing-safe compare.)
    if not user or not hmac.compare_digest(user.userkey, x_auth_key):
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

    # This endpoint is unauthenticated (KOReader's register button), so it must
    # never attach the credential to a Tome account — matching by username would
    # let anyone claim an account's sync identity just by knowing the username.
    # Linking happens through the authenticated path (Settings → KOReader Sync,
    # POST /api/auth/me/kosync), which also reclaims a squatted name by
    # overwriting its key.
    kosync_user = KOSyncUser(
        username=username,
        userkey=password,
        user_id=None,
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

    # Cross-reference to Tome book and update read status. Deterministic
    # ko-hash identity first; the legacy "next unknown push claims the oldest
    # pending OPDS download" heuristic only as fallback for files Tome never
    # hashed (it guesses, and a wrong guess writes status onto the wrong book).
    if user.user_id:
        doc_map = _find_or_hash_link(db, user.user_id, document)

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
            # Shared sticky-completion rule (same as web/plugin/manual writes):
            # position-sync semantics (monotonic=False tracks the report,
            # downward included), completion is sticky, finishes at 0.99.
            # Deliberately NOT TomeSyncPosition — see module docstring.
            from backend.services.book_progress import apply_progress_to_status
            apply_progress_to_status(
                db, user_id=user.user_id, book_id=doc_map.book_id,
                pct=float(percentage), monotonic=False,
            )
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

    # Bridge (issue #156): if Tome knows a newer position for this book —
    # web reader or a TomeSync-plugin device — serve that instead of the last
    # KOSync push, so a KOSync client picks up reading done elsewhere.
    # Read-only: nothing here writes plugin-visible state. The percentage is
    # the reliable cross-client signal; the locator string is included as-is
    # (an xpointer from a KOReader-family device resolves exactly, a web CFI
    # doesn't — clients like Readest then fall back to the percentage).
    if user.user_id:
        doc_map = _find_or_hash_link(db, user.user_id, document)
        if doc_map:
            from backend.models.tome_sync import TomeSyncPosition
            pos = (
                db.query(TomeSyncPosition)
                .filter(
                    TomeSyncPosition.user_id == user.user_id,
                    TomeSyncPosition.book_id == doc_map.book_id,
                )
                .first()
            )
            if pos is not None and pos.updated_at is not None:
                pos_ts = _epoch(pos.updated_at)
                if entry is None or pos_ts > entry.timestamp:
                    return {
                        "document": document,
                        "percentage": float(pos.percentage or 0.0),
                        "progress": pos.progress or str(pos.percentage or 0.0),
                        "device": pos.device or "web",
                        "device_id": None,
                        "timestamp": pos_ts,
                    }

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
