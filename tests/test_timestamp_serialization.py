"""API timestamps must be explicit UTC (Z-suffixed).

All datetimes are stored naive-UTC (datetime.utcnow). If an endpoint emits
them without a timezone suffix, ``new Date(iso)`` in the browser parses them
as *local* time, and every relative timestamp ("2h ago") drifts by the
viewer's UTC offset. Reported against the dashboard Reading Log (sessions
recorded minutes ago showed "2h ago" in CEST); the same class of bug lived in
notifications and API tokens.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.book import Book
from backend.models.notification import Notification
from backend.models.tome_sync import ReadingSession
from backend.models.user import User, UserPermission


def _make_member(db: Session, username: str = "ts_member") -> tuple[User, str]:
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
    db.add(UserPermission(user_id=user.id, can_upload=True, can_download=True))
    db.flush()
    return user, create_access_token(subject=user.id)


def _utc_z(value: str | None) -> bool:
    return value is not None and value.endswith("Z")


def test_home_activity_timestamps_are_utc_z(client: TestClient, db: Session):
    user, token = _make_member(db, "ts_home")
    book = Book(title="TS Book", author="A", added_by=user.id)
    db.add(book)
    db.flush()
    db.add(ReadingSession(
        user_id=user.id,
        book_id=book.id,
        started_at=datetime.utcnow(),
        ended_at=datetime.utcnow(),
        duration_seconds=60,
    ))
    db.commit()

    client.headers["Authorization"] = f"Bearer {token}"
    resp = client.get("/api/home/activity")
    assert resp.status_code == 200
    entries = resp.json()
    assert entries, "expected the seeded session in the activity log"
    assert all(_utc_z(e["started_at"]) for e in entries)


def test_notification_timestamps_are_utc_z(client: TestClient, db: Session):
    user, token = _make_member(db, "ts_notif")
    db.add(Notification(user_id=user.id, kind="wish_fulfilled", title="t"))
    db.commit()

    client.headers["Authorization"] = f"Bearer {token}"
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    items = resp.json()
    assert items
    assert all(_utc_z(n["created_at"]) for n in items)


def test_api_token_timestamps_are_utc_z(client: TestClient, db: Session):
    user, token = _make_member(db, "ts_tokens")
    client.headers["Authorization"] = f"Bearer {token}"
    created = client.post("/api/tokens/", json={"name": "ts-test"})
    assert created.status_code in (200, 201)

    resp = client.get("/api/tokens/")
    assert resp.status_code == 200
    items = resp.json()
    assert items
    assert all(_utc_z(t["created_at"]) for t in items)
    # last_used_at / revoked_at are None here; the serializer covers them too.
