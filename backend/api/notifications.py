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
from backend.models.notification import Notification
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
