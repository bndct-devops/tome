"""Tests for GET /api/books/{book_id}/reading-stats."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.tome_sync import ReadingSession
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_session(
    db: Session,
    user_id: int,
    book_id: int,
    started_at: datetime,
    duration_seconds: int = 600,
    pages_turned: int = 20,
) -> ReadingSession:
    s = ReadingSession(
        user_id=user_id,
        book_id=book_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        pages_turned=pages_turned,
    )
    db.add(s)
    db.flush()
    return s


def _get_stats(client: TestClient, book_id: int) -> dict:
    resp = client.get(f"/api/books/{book_id}/reading-stats")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_no_sessions_returns_zero_own(client: TestClient, make_book):
    """When no sessions exist, own stats should all be zero/null."""
    book = make_book(title="Unread Book")
    data = _get_stats(client, book.id)
    own = data["own"]
    assert own["sessions"] == 0
    assert own["total_seconds"] == 0
    assert own["pages_turned"] == 0
    assert own["session_timeline"] == []
    assert own["estimated_finish_seconds"] is None


def test_own_stats_aggregate(client: TestClient, make_book, admin_user, db: Session):
    """Sessions are summed correctly for the current user."""
    user, _ = admin_user
    book = make_book(title="Stats Book")
    now = datetime.utcnow()

    _make_session(db, user.id, book.id, now - timedelta(days=5), duration_seconds=3600, pages_turned=60)
    _make_session(db, user.id, book.id, now - timedelta(days=3), duration_seconds=1800, pages_turned=30)
    db.flush()

    data = _get_stats(client, book.id)
    own = data["own"]

    assert own["sessions"] == 2
    assert own["total_seconds"] == 5400
    assert own["pages_turned"] == 90
    assert own["avg_session_seconds"] == 2700
    # pace: 90 pages / 90 minutes = 1.0 pg/min
    assert own["pace_pages_per_min"] == pytest.approx(1.0)
    assert own["first_read"] is not None
    assert own["last_read"] is not None
    assert len(own["session_timeline"]) == 2


def test_estimated_finish_requires_progress(client: TestClient, make_book, admin_user, db: Session):
    """estimated_finish_seconds is None when progress is not set."""
    user, _ = admin_user
    book = make_book(title="No Progress Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=900)
    db.flush()

    data = _get_stats(client, book.id)
    # No UserBookStatus row → progress is None → no estimate
    assert data["own"]["estimated_finish_seconds"] is None


def test_estimated_finish_with_progress(client: TestClient, make_book, admin_user, db: Session):
    """estimated_finish_seconds is computed when progress is in (0, 1)."""
    user, _ = admin_user
    book = make_book(title="Progress Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=1200)
    # 25% done = 1200s spent → 3600s estimated remaining
    ubs = UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.25)
    db.add(ubs)
    db.flush()

    data = _get_stats(client, book.id)
    own = data["own"]
    assert own["status"] == "reading"
    assert own["progress"] == pytest.approx(0.25)
    # T/p*(1-p) = 1200/0.25*0.75 = 3600
    assert own["estimated_finish_seconds"] == pytest.approx(3600, abs=1)


def test_aggregate_only_for_admin(client: TestClient, make_book, admin_user, db: Session):
    """aggregate field is present for admins and absent for regular users."""
    user, _ = admin_user
    book = make_book(title="Aggregate Test Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=600)
    db.flush()

    # Admin sees aggregate
    data = _get_stats(client, book.id)
    assert data["aggregate"] is not None
    assert data["aggregate"]["total_sessions"] == 1
    assert data["aggregate"]["distinct_readers"] == 1

    # Non-admin does not see aggregate
    member = User(
        username="member1",
        email="member1@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    member_token = create_access_token(subject=member.id)

    from backend.main import create_app
    from backend.core.database import get_db

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    from starlette.testclient import TestClient as TC
    with TC(app, raise_server_exceptions=True) as member_client:
        member_client.headers["Authorization"] = f"Bearer {member_token}"
        resp = member_client.get(f"/api/books/{book.id}/reading-stats")
        assert resp.status_code == 200
        assert resp.json()["aggregate"] is None

    app.dependency_overrides.clear()


def test_404_for_missing_book(client: TestClient):
    """Non-existent book returns 404."""
    resp = client.get("/api/books/999999/reading-stats")
    assert resp.status_code == 404


def test_session_timeline_ordered_by_date(client: TestClient, make_book, admin_user, db: Session):
    """session_timeline is ordered chronologically."""
    user, _ = admin_user
    book = make_book(title="Timeline Book")
    now = datetime.utcnow()
    # Insert out of order
    _make_session(db, user.id, book.id, now - timedelta(days=1))
    _make_session(db, user.id, book.id, now - timedelta(days=10))
    _make_session(db, user.id, book.id, now - timedelta(days=5))
    db.flush()

    data = _get_stats(client, book.id)
    dates = [d["date"] for d in data["own"]["session_timeline"]]
    assert dates == sorted(dates)
