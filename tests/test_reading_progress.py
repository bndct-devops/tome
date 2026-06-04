"""Tests for reading progress sync correctness.

Covers:
- Completion stickiness in put_position (KOReader/TomeSync)
- Completion stickiness in post_session (KOReader/TomeSync)
- Web progress sync scale bug (/ 100.0 removed)

Note: tome-sync endpoints use _get_api_key_user (API key only, not JWT).
The JWT `client` fixture cannot authenticate against them. We create an API
key row directly and use a `tome_*` bearer — mirroring test_tomesync_selfupdate.py.
"""
from datetime import datetime

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import ApiKey, ReadingSession, TomeSyncPosition


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(db: Session, username: str, role: str = "member") -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=(role == "admin"),
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(UserPermission(
        user_id=user.id,
        can_upload=True,
        can_download=True,
        can_use_kosync=True,
    ))
    db.flush()
    return user, create_access_token(subject=user.id)


def _create_api_key(db: Session, user: User) -> str:
    """Insert an ApiKey row and return the plaintext key."""
    plaintext = ApiKey.generate()
    db.add(ApiKey(
        user_id=user.id,
        key_hash=ApiKey.hash_key(plaintext),
        key_prefix=plaintext[:11],
        label="test",
    ))
    db.flush()
    return plaintext


# ── shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def ts_client(db: Session):
    """Yield (TestClient, db, user, jwt_token, api_key_plaintext).

    The client has NO default Authorization header so we can set it per-call.
    App dependency is overridden to use the test db.
    """
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    user, jwt_token = _make_user(db, "tsuser", "member")
    api_key = _create_api_key(db, user)

    with TestClient(app, raise_server_exceptions=True) as c:
        # Yield order: client, db, user, jwt_token, api_key
        yield c, db, user, jwt_token, api_key

    app.dependency_overrides.clear()


# ── tests ─────────────────────────────────────────────────────────────────────

# 1. put_position lower pct on a "read" book — status stays read/1.0, BUT
#    TomeSyncPosition.percentage DOES update to the pushed (lower) value.
def test_put_position_does_not_unfinish_read_book(ts_client, make_book):
    c, db, user, _jwt, api_key = ts_client
    book = make_book(title="Already Finished")

    # Seed a completed status
    status = UserBookStatus(
        user_id=user.id, book_id=book.id,
        status="read", progress_pct=1.0,
    )
    db.add(status)
    db.flush()

    headers = {"Authorization": f"Bearer {api_key}"}
    r = c.put(
        f"/api/tome-sync/position/{book.id}",
        json={"percentage": 0.42, "progress": "epubcfi(/6/8!/4/2/4:0)", "device": "kindle"},
        headers=headers,
    )
    assert r.status_code == 200

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs is not None
    assert ubs.status == "read"
    assert ubs.progress_pct == pytest.approx(1.0)

    # TomeSyncPosition DOES track the device (resume position)
    pos = db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).first()
    assert pos is not None
    assert pos.percentage == pytest.approx(0.42)


# 2. put_position with pct >= 0.99 on a fresh book → status "read", progress_pct == 1.0
def test_put_position_finish_sets_status_read_and_normalizes(ts_client, make_book):
    c, db, user, _jwt, api_key = ts_client
    book = make_book(title="About To Finish")

    headers = {"Authorization": f"Bearer {api_key}"}
    r = c.put(
        f"/api/tome-sync/position/{book.id}",
        json={"percentage": 0.995, "device": "kindle"},
        headers=headers,
    )
    assert r.status_code == 200

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs is not None
    assert ubs.status == "read"
    assert ubs.progress_pct == pytest.approx(1.0)


# 3. put_position while "reading" with a lower pct than current → progress follows device
def test_put_position_reading_tracks_device_unconditionally(ts_client, make_book):
    c, db, user, _jwt, api_key = ts_client
    book = make_book(title="In Progress")

    status = UserBookStatus(
        user_id=user.id, book_id=book.id,
        status="reading", progress_pct=0.8,
    )
    db.add(status)
    db.flush()

    headers = {"Authorization": f"Bearer {api_key}"}
    r = c.put(
        f"/api/tome-sync/position/{book.id}",
        json={"percentage": 0.3, "device": "kindle"},
        headers=headers,
    )
    assert r.status_code == 200

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs is not None
    assert ubs.status == "reading"
    assert ubs.progress_pct == pytest.approx(0.3)


# 4. Regression (book 40 scenario): read/1.0, push 0.78 → stays read/1.0;
#    TomeSyncPosition.percentage == 0.78.
def test_regression_book40_read_book_position_update(ts_client, make_book):
    c, db, user, _jwt, api_key = ts_client
    book = make_book(title="Book 40")

    db.add(UserBookStatus(
        user_id=user.id, book_id=book.id,
        status="read", progress_pct=1.0,
    ))
    db.flush()

    headers = {"Authorization": f"Bearer {api_key}"}
    r = c.put(
        f"/api/tome-sync/position/{book.id}",
        json={"percentage": 0.78, "device": "kindle"},
        headers=headers,
    )
    assert r.status_code == 200

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs.status == "read"
    assert ubs.progress_pct == pytest.approx(1.0)

    pos = db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).first()
    assert pos.percentage == pytest.approx(0.78)


# 5. Web finish: PUT /api/books/{id}/status {status:"read", progress_pct:1}
#    → TomeSyncPosition.percentage == 1.0 (NOT 0.01) and UserBookStatus.progress_pct == 1.0
def test_web_finish_syncs_position_at_correct_scale(ts_client, make_book):
    c, db, user, jwt_token, _api_key = ts_client
    book = make_book(title="Web Finished Book")

    # Use JWT auth for the web endpoint
    r = c.put(
        f"/api/books/{book.id}/status",
        json={"status": "read", "progress_pct": 1.0},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs is not None
    assert ubs.progress_pct == pytest.approx(1.0)

    pos = db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).first()
    assert pos is not None
    assert pos.percentage == pytest.approx(1.0), (
        f"Expected 1.0 but got {pos.percentage} — / 100 scale bug still present"
    )


# 6. Web mid-read: PUT /api/books/{id}/status {status:"reading", progress_pct:0.5}
#    → TomeSyncPosition.percentage == 0.5
def test_web_reading_syncs_position_at_correct_scale(ts_client, make_book):
    c, db, user, jwt_token, _api_key = ts_client
    book = make_book(title="Web Mid-Read Book")

    r = c.put(
        f"/api/books/{book.id}/status",
        json={"status": "reading", "progress_pct": 0.5},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200

    db.expire_all()
    pos = db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).first()
    assert pos is not None
    assert pos.percentage == pytest.approx(0.5), (
        f"Expected 0.5 but got {pos.percentage} — / 100 scale bug still present"
    )


# 7. post_session promoting unread→read with progress_end 0.995 → read/1.0;
#    a later session with lower progress_end leaves it read/1.0.
def test_post_session_finish_and_later_session_sticky(ts_client, make_book):
    c, db, user, _jwt, api_key = ts_client
    book = make_book(title="Session Finisher")

    headers = {"Authorization": f"Bearer {api_key}"}

    # First session — finishes the book
    r = c.post("/api/tome-sync/session", json={
        "book_id": book.id,
        "started_at": "2026-01-01T10:00:00Z",
        "ended_at": "2026-01-01T11:00:00Z",
        "duration_seconds": 3600,
        "progress_start": 0.8,
        "progress_end": 0.995,
        "device": "kindle",
    }, headers=headers)
    assert r.status_code == 201

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs is not None
    assert ubs.status == "read"
    assert ubs.progress_pct == pytest.approx(1.0)

    # Second session — lower progress_end (re-read from the start)
    r = c.post("/api/tome-sync/session", json={
        "book_id": book.id,
        "started_at": "2026-02-01T10:00:00Z",
        "ended_at": "2026-02-01T10:30:00Z",
        "duration_seconds": 1800,
        "progress_start": 0.0,
        "progress_end": 0.2,
        "device": "kindle",
    }, headers=headers)
    assert r.status_code == 201

    db.expire_all()
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert ubs.status == "read", "Completion must be sticky after a later session"
    assert ubs.progress_pct == pytest.approx(1.0)


# 8. Web reading session scale: PUT /api/books/{id}/status {status:"reading", progress_pct:0.5}
#    → created ReadingSession.progress_end == 0.5 (NOT 0.005).
def test_web_reading_session_scale(ts_client, make_book):
    c, db, user, jwt_token, _api_key = ts_client
    book = make_book(title="Session Scale Book")

    r = c.put(
        f"/api/books/{book.id}/status",
        json={"status": "reading", "progress_pct": 0.5},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200

    db.expire_all()
    session = (
        db.query(ReadingSession)
        .filter_by(user_id=user.id, book_id=book.id, device="web")
        .first()
    )
    assert session is not None
    assert session.progress_end == pytest.approx(0.5), (
        f"Expected progress_end=0.5 but got {session.progress_end} — / 100 scale bug still present"
    )
    assert session.progress_start == pytest.approx(0.5)
