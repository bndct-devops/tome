"""Nightlamp link API (Settings > Nightlamp), mounted at /api/platform.

Every route 404s when the feature is off (TOME_PLATFORM_ENABLED=false), and needs a member or admin
(guests get 403). Linking follows the Nightlamp contract §1: Tome asks
Nightlamp for a one-time URL, the browser opens it in a new tab, and Tome polls
until Nightlamp hands over the handle and the push token. The poll secret
lives only in memory (5 minutes); the push token is stored encrypted.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import encrypt_secret
from backend.core.database import get_db
from backend.core.permissions import require_role
from backend.core.security import get_current_user
from backend.models.platform import PlatformCoverSent, PlatformLink
from backend.models.user import User
from backend.services import platform_push
from backend.services.audit import audit


def _require_enabled() -> None:
    if not platform_push.enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _member(current_user: User = Depends(get_current_user)) -> User:
    require_role(current_user, "member")
    return current_user


# The switch guards every route here, so a route added later cannot skip it.
router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[Depends(_require_enabled)])

PENDING_TTL = 5 * 60


class _Pending:
    __slots__ = ("request_id", "poll_secret", "expires", "timezone", "mode")

    def __init__(self, request_id: str, poll_secret: str, timezone: str, mode: str):
        self.request_id = request_id
        self.poll_secret = poll_secret
        self.expires = time.monotonic() + PENDING_TTL
        self.timezone = timezone
        self.mode = mode


_pending: dict[int, _Pending] = {}
_pending_guard = threading.Lock()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _link_row(db: Session, user: User) -> Optional[PlatformLink]:
    return db.query(PlatformLink).filter(PlatformLink.user_id == user.id).first()


@router.get("/status")
def platform_status(db: Session = Depends(get_db), user: User = Depends(_member)):
    link = _link_row(db, user)
    revoked = bool(link and link.revoked_at)
    return {
        "enabled": True,
        "linked": bool(link) and not revoked,
        "handle": link.handle if link else None,
        "timezone": link.timezone if link else None,
        "share_titles": bool(link.share_titles) if link else False,
        "linked_at": _iso(link.linked_at) if link else None,
        "last_push_at": _iso(link.last_push_at) if link else None,
        "last_push_error": link.last_push_error if link else None,
        "revoked": revoked,
        "pushing": platform_push.is_pushing(user.id),
        "platform_name": settings.platform_name,
        "platform_url": platform_push.base_url(),
    }


class LinkBody(BaseModel):
    timezone: str = Field(default="UTC", max_length=64)


def _first_admin_name(db: Session) -> str:
    admin = (db.query(User).filter((User.is_admin == True) | (User.role == "admin"))  # noqa: E712
             .order_by(User.id).first())
    return admin.username if admin else "Tome"


def _request_link(db: Session, user: User, tz: str, mode: str) -> dict:
    iid, secret = platform_push.instance_identity(db)
    role = "admin" if (user.is_admin or user.role == "admin") else user.role
    body = {
        "schema_version": platform_push.SCHEMA_VERSION,
        "instance": {"id": iid, "secret": secret, "name_hint": f"{_first_admin_name(db)}'s Tome"},
        "tome_user": {"id": platform_push.tome_user_id(secret, user.id), "display_name": user.username,
                      "role": role},
        "timezone": tz,
    }
    try:
        res = platform_push.call("POST", "/api/link/request", json_body=body)
    except platform_push.PlatformError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    request_id, poll_secret, url = res.get("request_id"), res.get("poll_secret"), res.get("url")
    if not (request_id and poll_secret and url):
        raise HTTPException(status_code=502, detail=f"{settings.platform_name} sent an incomplete answer")
    with _pending_guard:
        _pending[user.id] = _Pending(str(request_id), str(poll_secret), tz, mode)
    audit(db, "platform.link_requested", user_id=user.id, username=user.username,
          details={"mode": mode})
    return {"url": url, "request_id": request_id}


@router.post("/link")
def platform_link(body: LinkBody, db: Session = Depends(get_db), user: User = Depends(_member)):
    tz = body.timezone if platform_push.valid_zone(body.timezone) else "UTC"
    return _request_link(db, user, tz, "link")


@router.post("/open")
def platform_open(db: Session = Depends(get_db), user: User = Depends(_member)):
    link = _link_row(db, user)
    if link is None or link.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Not linked")
    return _request_link(db, user, link.timezone, "open")


@router.get("/link/{request_id}")
def platform_link_poll(request_id: str, db: Session = Depends(get_db), user: User = Depends(_member)):
    with _pending_guard:
        pending = _pending.get(user.id)
    if pending is None or pending.request_id != request_id or pending.expires <= time.monotonic():
        return {"status": "expired"}
    try:
        res = platform_push.call("GET", f"/api/link/request/{pending.request_id}",
                                 params={"poll": pending.poll_secret})
    except platform_push.PlatformError as exc:
        if exc.status == 404:
            return {"status": "expired"}
        raise HTTPException(status_code=502, detail=str(exc))
    status = res.get("status")
    if status == "pending":
        return {"status": "pending"}
    if status != "done":
        with _pending_guard:
            _pending.pop(user.id, None)
        return {"status": "expired"}

    handle = str(res.get("handle") or "")
    token = res.get("push_token")
    with _pending_guard:
        _pending.pop(user.id, None)
    link = _link_row(db, user)
    if not token:
        # The token is handed out once; a second poll after it was stored is fine.
        return {"status": "done", "handle": link.handle if link else handle}

    full = pending.mode == "link" or link is None or link.revoked_at is not None
    if link is None:
        link = PlatformLink(user_id=user.id, handle=handle, push_token_enc=encrypt_secret(token),
                            timezone=pending.timezone, share_titles=False, linked_at=datetime.utcnow())
        db.add(link)
    else:
        link.handle = handle or link.handle
        link.push_token_enc = encrypt_secret(token)
        if link.revoked_at is not None or pending.mode == "link":
            link.linked_at = datetime.utcnow()
            link.timezone = pending.timezone
        link.revoked_at = None
        link.last_push_error = None
    if full:
        link.pushed_through = None
    db.commit()
    audit(db, "platform.linked", user_id=user.id, username=user.username,
          details={"mode": pending.mode})
    if full:
        platform_push.spawn_push(user.id, full=True)
    return {"status": "done", "handle": link.handle}


class SettingsBody(BaseModel):
    share_titles: Optional[bool] = None
    timezone: Optional[str] = Field(default=None, max_length=64)


@router.patch("/settings")
def platform_settings(body: SettingsBody, db: Session = Depends(get_db), user: User = Depends(_member)):
    link = _link_row(db, user)
    if link is None:
        raise HTTPException(status_code=409, detail="Not linked")
    changed: list[str] = []
    resync = False
    push_now = False
    if body.timezone is not None and body.timezone != link.timezone:
        if not platform_push.valid_zone(body.timezone):
            raise HTTPException(status_code=422, detail="Unknown timezone")
        link.timezone = body.timezone
        changed.append("timezone")
        resync = True
    if body.share_titles is not None and body.share_titles != link.share_titles:
        link.share_titles = body.share_titles
        changed.append("share_titles")
        if body.share_titles:
            resync = True
        else:
            push_now = True
    db.commit()
    if changed:
        audit(db, "platform.settings_changed", user_id=user.id, username=user.username,
              details={"fields": changed})
    if link.revoked_at is None and (resync or push_now):
        platform_push.spawn_push(user.id, full=resync)
    return platform_status(db=db, user=user)


@router.post("/resync")
def platform_resync(db: Session = Depends(get_db), user: User = Depends(_member)):
    link = _link_row(db, user)
    if link is None or link.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Not linked")
    audit(db, "platform.resync", user_id=user.id, username=user.username)
    platform_push.spawn_push(user.id, full=True)
    return {"started": True}


@router.post("/unlink")
def platform_unlink(db: Session = Depends(get_db), user: User = Depends(_member)):
    link = _link_row(db, user)
    with _pending_guard:
        _pending.pop(user.id, None)
    if link is None:
        return {"ok": True}
    db.delete(link)
    db.query(PlatformCoverSent).filter(PlatformCoverSent.user_id == user.id).delete()
    db.commit()
    audit(db, "platform.unlinked", user_id=user.id, username=user.username)
    return {"ok": True}
