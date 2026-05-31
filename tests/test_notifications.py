"""Tests for the notifications API.

Covers:
- List notifications (all and unread-only filter)
- Mark single notification as read
- Mark all as read
- Ownership enforced (cannot mark another user's notification)
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.notification import Notification
from backend.models.user import User, UserPermission


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_member(db: Session, username: str = "notif_member") -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id, can_upload=True, can_download=True)
    db.add(perms)
    db.flush()
    token = create_access_token(subject=user.id)
    return user, token


# ── list notifications ────────────────────────────────────────────────────────

def test_list_notifications_empty(client: TestClient, db: Session, admin_user):
    """Empty list when no notifications."""
    _, admin_token = admin_user
    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_notifications_shows_own(client: TestClient, db: Session, admin_user):
    """Notifications for the current user are returned."""
    user_admin, admin_token = admin_user
    db.add(Notification(user_id=user_admin.id, kind="k", title="My Notif", read=False))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "My Notif"
    assert data[0]["read"] is False


def test_list_notifications_not_other_users(client: TestClient, db: Session, admin_user):
    """Notifications from other users are not returned."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "notif_other")

    # Admin notification should not appear for member
    db.add(Notification(user_id=user_admin.id, kind="k", title="Admin Notif", read=False))
    db.flush()

    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_notifications_unread_filter(client: TestClient, db: Session, admin_user):
    """?unread=true returns only unread notifications."""
    user_admin, admin_token = admin_user
    db.add(Notification(user_id=user_admin.id, kind="k", title="Unread One", read=False))
    db.add(Notification(user_id=user_admin.id, kind="k", title="Read One", read=True))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/notifications?unread=true")
    assert resp.status_code == 200
    data = resp.json()
    assert all(not n["read"] for n in data)
    titles = [n["title"] for n in data]
    assert "Unread One" in titles
    assert "Read One" not in titles


# ── mark read ─────────────────────────────────────────────────────────────────

def test_mark_single_notification_read(client: TestClient, db: Session, admin_user):
    """POST /notifications/{id}/read sets read=True."""
    user_admin, admin_token = admin_user
    notif = Notification(user_id=user_admin.id, kind="k", title="Single Read", read=False)
    db.add(notif)
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post(f"/api/notifications/{notif.id}/read")
    assert resp.status_code == 200
    assert resp.json()["read"] is True

    db.refresh(notif)
    assert notif.read is True


def test_mark_nonexistent_notification_404(client: TestClient, db: Session, admin_user):
    """Marking a nonexistent notification returns 404."""
    _, admin_token = admin_user
    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post("/api/notifications/99999/read")
    assert resp.status_code == 404


def test_mark_other_users_notification_404(client: TestClient, db: Session, admin_user):
    """Cannot mark another user's notification — returns 404."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "notif_cross")

    notif = Notification(user_id=user_admin.id, kind="k", title="Admin Only", read=False)
    db.add(notif)
    db.flush()

    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.post(f"/api/notifications/{notif.id}/read")
    assert resp.status_code == 404


# ── mark all read ─────────────────────────────────────────────────────────────

def test_mark_all_read(client: TestClient, db: Session, admin_user):
    """POST /notifications/read-all marks all unread notifications as read."""
    user_admin, admin_token = admin_user
    for i in range(3):
        db.add(Notification(user_id=user_admin.id, kind="k", title=f"N{i}", read=False))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post("/api/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    unread = db.query(Notification).filter(
        Notification.user_id == user_admin.id,
        Notification.read == False,  # noqa: E712
    ).count()
    assert unread == 0


def test_mark_all_read_only_affects_own(client: TestClient, db: Session, admin_user):
    """mark-all only marks the current user's notifications, not others'."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "notif_own_only")

    db.add(Notification(user_id=member.id, kind="k", title="Member Notif", read=False))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    client.post("/api/notifications/read-all")

    # Member's notification should still be unread
    member_unread = db.query(Notification).filter(
        Notification.user_id == member.id,
        Notification.read == False,  # noqa: E712
    ).count()
    assert member_unread == 1
