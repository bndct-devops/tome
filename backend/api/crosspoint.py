"""Tome's plugin for CrossPoint readers (experimental): the routes it calls.

The plugin itself is a few JSON files (``backend/services/crosspoint_plugin.py``).
They tell the firmware to call these routes: to link the reader with a code
shown on its screen, to deliver each reading session, and to fill the short
status list the reader shows under Plugins.

A linked reader holds a device key like the KOReader plugin's: stored hashed,
named, listed under Settings > Access and revocable there. It is of its own
kind (``ApiKey.KIND_READER``) and opens only the routes in this file, so a
lost SD card, or a link someone was talked into confirming, gives away
neither the account nor its books.

The path is under ``/api/v1`` because that is where the reader-facing sync
routes live. See ``backend/services/crosspoint_link.py`` for the linking
steps and ``backend/services/kosync_stats.py`` ("the CrossPoint plugin") for
what happens to a session.
"""
import logging
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.kosync import _find_or_hash_link
from backend.core.database import get_db
from backend.core.permissions import has_role
from backend.core.security import (
    get_current_user,
    get_current_user_no_api_token,
    refuse_if_password_change_pending,
)
from backend.core.urls import public_base_url
from backend.models.tome_sync import ApiKey
from backend.models.user import User
from backend.services import crosspoint_link as link
from backend.services import crosspoint_plugin
from backend.services import kosync_stats as stats
from backend.services.audit import audit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/crosspoint", tags=["crosspoint-plugin"])


def _member(user: User) -> User:
    if not has_role(user, "member"):
        raise HTTPException(status_code=403, detail="Linking a reader requires member role or above")
    return user


def _reader_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """The user a linked reader delivers for. Only a reader's own key is
    accepted here, and such a key is accepted nowhere else."""
    plaintext = (authorization or "").removeprefix("Bearer ").strip()
    api_key = (
        db.query(ApiKey).filter(ApiKey.key_hash == ApiKey.hash_reader_key(plaintext)).first()
        if plaintext else None
    )
    if api_key is None or api_key.kind != ApiKey.KIND_READER:
        raise HTTPException(status_code=401, detail="This reader is not linked")
    user = db.get(User, api_key.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="This reader is not linked")
    refuse_if_password_change_pending(user)
    _member(user)
    api_key.last_used_at = datetime.utcnow()
    db.commit()
    return user


# ── linking a reader (see services/crosspoint_link.py) ───────────────────────

@router.post("/device/code")
def start_link(request: Request, server_url: Optional[str] = None):
    """The reader asks for a code to show. No sign-in: it has none yet."""
    try:
        device_code, user_code = link.start()
    except link.Busy:
        raise HTTPException(status_code=429, detail="Too many readers are waiting to be linked. Try again in a few minutes.")
    base = public_base_url(request, server_url)
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": f"{base}/link-reader?code={user_code}",
        "interval": link.POLL_INTERVAL,
        "expires_in": link.PENDING_TTL,
    }


@router.post("/device/token")
def collect_key(body: dict[str, Any], db: Session = Depends(get_db)):
    """The reader asks whether its code was confirmed. The key is handed out
    once; the answers follow RFC 8628, which is what the firmware reads."""
    state, user_id = link.poll(body.get("device_code"))
    if state == "pending":
        return JSONResponse(status_code=400, content={"error": "authorization_pending"})
    user = db.get(User, user_id) if user_id is not None else None
    if state != "approved" or user is None or not user.is_active or not has_role(user, "member"):
        return JSONResponse(status_code=400, content={"error": "expired_token"})
    key_value = ApiKey.generate()
    api_key = ApiKey(
        user_id=user.id,
        key_hash=ApiKey.hash_reader_key(key_value),
        key_prefix=key_value[:11],
        label=f"{link.READER_KEY_LABEL} {datetime.utcnow().date().isoformat()}",
        kind=ApiKey.KIND_READER,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    audit(db, "plugin_key.created", user_id=user.id, username=user.username,
          resource_type="plugin_key", resource_id=api_key.id, resource_title=api_key.label,
          details={"prefix": api_key.key_prefix, "via": "reader_link"})
    return {"access_token": key_value, "token_type": "bearer"}


def _refuse_guessing(user: User) -> None:
    if link.too_many_wrong(user.id):
        raise HTTPException(status_code=429, detail="Too many wrong codes. Wait a few minutes and start again on the reader.")


@router.get("/device/{user_code}")
def link_waiting(user_code: str, current_user: User = Depends(get_current_user)):
    """Does a reader wait under this code? What the confirmation page asks
    before it offers the button."""
    _member(current_user)
    _refuse_guessing(current_user)
    if not link.waiting(user_code, current_user.id):
        raise HTTPException(status_code=404, detail="No reader is waiting with this code. Codes last ten minutes.")
    return {"code": link.normalise(user_code)}


@router.post("/device/approve")
def approve_link(
    body: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_no_api_token),
):
    """The signed-in user confirms the code their reader shows."""
    _member(current_user)
    _refuse_guessing(current_user)
    if not link.approve(body.get("code"), current_user.id):
        raise HTTPException(status_code=404, detail="No reader is waiting with this code. Codes last ten minutes.")
    audit(db, "crosspoint.reader_link_approved", user_id=current_user.id, username=current_user.username,
          ip=request.client.host if request.client else None)
    return {"linked": True}


# ── what a linked reader sends and shows ─────────────────────────────────────

def _store(db: Session, write):
    """A reader that timed out retries while its first request may still be
    running; the second then hits a unique constraint. Running it again finds
    the row."""
    try:
        return write()
    except IntegrityError:
        db.rollback()
        return write()


@router.post("/sessions")
def post_session(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(_reader_user),
):
    def resolve_book(document: str) -> Optional[int]:
        doc_map = _find_or_hash_link(db, user.id, document)
        return doc_map.book_id if doc_map else None

    now = int(time.time())
    try:
        outcome = _store(db, lambda: stats.store_plugin_session(db, user.id, body, resolve_book, now))
    except stats.Rejected as exc:
        # 200 on purpose: any other answer keeps the event at the head of the
        # reader's queue, and it would never get past it. Nothing was written:
        # a session is refused before the first write.
        log.info("CrossPoint plugin session dropped (user %s): %s", user.id, exc.detail)
        return {"stored": False, "reason": exc.detail}
    return {"stored": True, "outcome": outcome}


def _clock(seconds: int) -> str:
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), user: User = Depends(_reader_user)):
    """The few lines the reader shows when the plugin is opened: that it is
    linked, and what Tome has received from it."""
    summary = stats.plugin_summary(db, user.id)
    items = [{
        "id": "account",
        "title": f"Linked to {user.username}",
        "subtitle": "Sessions are sent when the reader goes to sleep",
    }]
    if summary["sessions"]:
        items.append({
            "id": "received",
            "title": "1 session received" if summary["sessions"] == 1 else f"{summary['sessions']} sessions received",
            "subtitle": f"{_clock(summary['seconds'])} of reading",
        })
        items.append({
            "id": "last",
            "title": "Last session",
            "subtitle": summary["last_ended_at"].strftime("%Y-%m-%d %H:%M UTC"),
        })
    else:
        items.append({"id": "received", "title": "No sessions received yet", "subtitle": "Read a few pages, then let the reader sleep"})
    return {"items": items}


# ── for the web UI ────────────────────────────────────────────────────────────

@router.get("/status")
def status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Linked readers and what they have delivered (Settings > Xteink readers)."""
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == current_user.id, ApiKey.kind == ApiKey.KIND_READER)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    summary = stats.plugin_summary(db, current_user.id)
    return {
        "readers": [{
            "id": k.id,
            "label": k.label,
            "created_at": k.created_at.isoformat() + "Z",
            "last_used_at": k.last_used_at.isoformat() + "Z" if k.last_used_at else None,
        } for k in keys],
        "sessions": summary["sessions"],
        "seconds": summary["seconds"],
        "last_session_at": summary["last_ended_at"].isoformat() + "Z" if summary["last_ended_at"] else None,
    }


@router.get("/plugin.zip")
def plugin_zip(
    request: Request,
    server_url: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """The plugin folder as a ZIP. It holds this server's address and nothing
    about the account: the reader is linked afterwards, with a code."""
    data = crosspoint_plugin.build_zip(public_base_url(request, server_url))
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="tome-crosspoint-plugin.zip"'},
    )
