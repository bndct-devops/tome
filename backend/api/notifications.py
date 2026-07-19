"""Notifications API — list and mark in-app notifications.

Mounted at /api, tags=["notifications"].
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.notification import Notification, NotificationChannel
from backend.models.user import User

router = APIRouter(tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    user_id: int
    kind: str
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def _utc_z(self, dt: datetime) -> str:
        # Stored naive-UTC; emit an explicit Z or browsers parse it as local
        # time and relative timestamps drift by the viewer's UTC offset.
        return dt.isoformat() + "Z"


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    unread: Optional[bool] = Query(None, description="Filter to unread only when true"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current user's notifications, newest first."""
    q = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread is True:
        q = q.filter(Notification.read == False)  # noqa: E712
    return q.order_by(Notification.created_at.desc()).all()


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a single notification as read (ownership enforced)."""
    n = db.get(Notification, notification_id)
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    if n.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Notification not found")

    n.read = True
    db.commit()
    db.refresh(n)
    return n


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all of the current user's notifications as read."""
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.read == False,  # noqa: E712
    ).update({"read": True})
    db.commit()
    return {"ok": True}


# ── Outbound channels (ntfy / Gotify / webhook) ───────────────────────────────

CHANNEL_KINDS = ("ntfy", "gotify", "webhook")


class ChannelIn(BaseModel):
    kind: str
    url: str
    token: Optional[str] = None


class ChannelOut(BaseModel):
    id: int
    kind: str
    url: str
    has_token: bool
    enabled: bool


def _channel_out(c: NotificationChannel) -> ChannelOut:
    return ChannelOut(id=c.id, kind=c.kind, url=c.url,
                      has_token=bool(c.token), enabled=c.enabled)


@router.get("/notification-channels", response_model=list[ChannelOut])
def list_channels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.user_id == current_user.id)
        .order_by(NotificationChannel.id)
        .all()
    )
    return [_channel_out(c) for c in rows]


@router.post("/notification-channels", response_model=ChannelOut, status_code=201)
def create_channel(
    body: ChannelIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.kind not in CHANNEL_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {CHANNEL_KINDS}")
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="url must be http(s)")
    c = NotificationChannel(user_id=current_user.id, kind=body.kind,
                            url=url, token=(body.token or None))
    db.add(c)
    db.commit()
    db.refresh(c)
    return _channel_out(c)


@router.delete("/notification-channels/{channel_id}")
def delete_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.get(NotificationChannel, channel_id)
    if not c or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Channel not found")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/notification-channels/{channel_id}/toggle", response_model=ChannelOut)
def toggle_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.get(NotificationChannel, channel_id)
    if not c or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Channel not found")
    c.enabled = not c.enabled
    db.commit()
    db.refresh(c)
    return _channel_out(c)


@router.post("/notification-channels/{channel_id}/test")
def test_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a test notification synchronously and report the outcome — the
    only way to debug a typo'd topic URL at setup time."""
    from backend.services.outbound_notifications import deliver

    c = db.get(NotificationChannel, channel_id)
    if not c or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        deliver(c.kind, c.url, c.token, {
            "kind": "test",
            "title": "Tome test notification",
            "body": "Your channel is wired up correctly.",
            "link": "/",
        })
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not hidden
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True}
