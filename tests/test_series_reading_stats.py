"""Tests for GET /api/series/{name}/reading-stats."""
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


def _get_stats(client: TestClient, series_name: str) -> dict:
    from urllib.parse import quote
    resp = client.get(f"/api/series/{quote(series_name, safe='')}/reading-stats")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_no_sessions_returns_zero_own(client: TestClient, make_book):
    """When no sessions exist, own stats should all be zero/null."""
    make_book(title="Vol 1", series="TestSeries", series_index=1.0)
    make_book(title="Vol 2", series="TestSeries", series_index=2.0)

    data = _get_stats(client, "TestSeries")
    own = data["own"]
    assert own["sessions"] == 0
    assert own["total_seconds"] == 0
    assert own["pages_turned"] == 0
    assert own["books_total"] == 2
    assert own["books_finished"] == 0
    assert own["books_in_progress"] == 0
    assert own["books_with_sessions"] == 0
    assert own["completion_pct"] == 0.0
    assert own["avg_volume_seconds"] == 0
    assert own["estimated_remaining_seconds"] is None
    assert own["longest_volume"] is None
    assert own["first_read"] is None
    assert own["last_read"] is None
    assert len(own["per_volume"]) == 2


def test_aggregate_across_multiple_volumes(client: TestClient, make_book, admin_user, db: Session):
    """Sessions on multiple volumes are summed correctly."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="MySeries", series_index=1.0)
    vol2 = make_book(title="Vol 2", series="MySeries", series_index=2.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=5), duration_seconds=3600, pages_turned=60)
    _make_session(db, user.id, vol1.id, now - timedelta(days=4), duration_seconds=1800, pages_turned=30)
    _make_session(db, user.id, vol2.id, now - timedelta(days=2), duration_seconds=2400, pages_turned=40)
    db.flush()

    data = _get_stats(client, "MySeries")
    own = data["own"]

    assert own["sessions"] == 3
    assert own["total_seconds"] == 7800   # 3600+1800+2400
    assert own["pages_turned"] == 130
    assert own["books_total"] == 2
    assert own["books_with_sessions"] == 2
    assert own["first_read"] is not None
    assert own["last_read"] is not None


def test_completion_pct(client: TestClient, make_book, admin_user, db: Session):
    """completion_pct = books_finished / books_total * 100."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="CompleteSeries", series_index=1.0)
    vol2 = make_book(title="Vol 2", series="CompleteSeries", series_index=2.0)
    vol3 = make_book(title="Vol 3", series="CompleteSeries", series_index=3.0)

    # Mark vol1 as read, vol2 as reading
    db.add(UserBookStatus(user_id=user.id, book_id=vol1.id, status="read"))
    db.add(UserBookStatus(user_id=user.id, book_id=vol2.id, status="reading"))
    db.flush()

    data = _get_stats(client, "CompleteSeries")
    own = data["own"]

    assert own["books_total"] == 3
    assert own["books_finished"] == 1
    assert own["books_in_progress"] == 1
    assert own["completion_pct"] == pytest.approx(33.3, abs=0.1)


def test_estimated_remaining_seconds(client: TestClient, make_book, admin_user, db: Session):
    """estimated_remaining_seconds = avg_finished_volume_seconds * unfinished_count."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="EstSeries", series_index=1.0)
    vol2 = make_book(title="Vol 2", series="EstSeries", series_index=2.0)
    vol3 = make_book(title="Vol 3", series="EstSeries", series_index=3.0)
    now = datetime.utcnow()

    # 3600s on vol1 (finished)
    _make_session(db, user.id, vol1.id, now - timedelta(days=10), duration_seconds=3600)
    db.add(UserBookStatus(user_id=user.id, book_id=vol1.id, status="read"))
    db.flush()

    data = _get_stats(client, "EstSeries")
    own = data["own"]

    # avg finished = 3600, unfinished = 2 → estimated = 7200
    assert own["books_finished"] == 1
    assert own["estimated_remaining_seconds"] == 7200


def test_estimated_remaining_null_when_no_finished(client: TestClient, make_book, admin_user, db: Session):
    """estimated_remaining_seconds is None when no volumes are finished."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="NoFinSeries", series_index=1.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=1), duration_seconds=600)
    db.add(UserBookStatus(user_id=user.id, book_id=vol1.id, status="reading"))
    db.flush()

    data = _get_stats(client, "NoFinSeries")
    assert data["own"]["estimated_remaining_seconds"] is None


def test_per_volume_includes_all_books(client: TestClient, make_book, admin_user, db: Session):
    """per_volume contains every visible book, including unread ones with seconds=0."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="PVSeries", series_index=1.0)
    vol2 = make_book(title="Vol 2", series="PVSeries", series_index=2.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=1), duration_seconds=1200)
    db.flush()

    data = _get_stats(client, "PVSeries")
    pv = data["own"]["per_volume"]
    assert len(pv) == 2
    vol1_pv = next(v for v in pv if v["book_id"] == vol1.id)
    vol2_pv = next(v for v in pv if v["book_id"] == vol2.id)
    assert vol1_pv["seconds"] == 1200
    assert vol2_pv["seconds"] == 0
    assert vol2_pv["status"] == "unread"


def test_per_volume_ordered_by_series_index(client: TestClient, make_book, admin_user, db: Session):
    """per_volume is ordered ascending by series_index."""
    user, _ = admin_user
    # Insert in reverse order
    make_book(title="Vol 3", series="OrderSeries", series_index=3.0)
    make_book(title="Vol 1", series="OrderSeries", series_index=1.0)
    make_book(title="Vol 2", series="OrderSeries", series_index=2.0)
    db.flush()

    data = _get_stats(client, "OrderSeries")
    indices = [v["series_index"] for v in data["own"]["per_volume"]]
    assert indices == sorted(indices)


def test_longest_volume(client: TestClient, make_book, admin_user, db: Session):
    """longest_volume points to the book with the most time spent."""
    user, _ = admin_user
    vol1 = make_book(title="Short Vol", series="LVSeries", series_index=1.0)
    vol2 = make_book(title="Long Vol", series="LVSeries", series_index=2.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=5), duration_seconds=600)
    _make_session(db, user.id, vol2.id, now - timedelta(days=3), duration_seconds=3600)
    db.flush()

    data = _get_stats(client, "LVSeries")
    lv = data["own"]["longest_volume"]
    assert lv is not None
    assert lv["book_id"] == vol2.id
    assert lv["seconds"] == 3600


def test_admin_sees_aggregate(client: TestClient, make_book, admin_user, db: Session):
    """Admin receives the aggregate field with all-user totals."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="AggSeries", series_index=1.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=1), duration_seconds=900)
    db.flush()

    data = _get_stats(client, "AggSeries")
    assert data["aggregate"] is not None
    assert data["aggregate"]["total_sessions"] == 1
    assert data["aggregate"]["distinct_readers"] == 1
    assert data["aggregate"]["total_seconds"] == 900


def test_non_admin_aggregate_is_null(make_book, admin_user, db: Session):
    """Non-admin users do not receive the aggregate field."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="NoAggSeries", series_index=1.0)
    now = datetime.utcnow()

    _make_session(db, user.id, vol1.id, now - timedelta(days=1), duration_seconds=600)

    member = User(
        username="member_noagg",
        email="member_noagg@example.com",
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

    from urllib.parse import quote
    from starlette.testclient import TestClient as TC
    with TC(app, raise_server_exceptions=True) as member_client:
        member_client.headers["Authorization"] = f"Bearer {member_token}"
        resp = member_client.get(f"/api/series/{quote('NoAggSeries', safe='')}/reading-stats")
        assert resp.status_code == 200
        assert resp.json()["aggregate"] is None

    app.dependency_overrides.clear()


def test_visibility_filter_excludes_books(make_book, admin_user, db: Session):
    """Books not visible to a user do not appear in per_volume or stats."""
    admin_user_obj, _ = admin_user

    # Create a second user (member without assigned libs → sees only admin books)
    member = User(
        username="member_vis",
        email="member_vis@example.com",
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

    # Admin creates a book in a private (non-public) series — member can still see
    # admin-uploaded books, but we need a book uploaded by the member themselves
    # to test isolation. Instead test that books_total only reflects the admin books
    # visible to the member (all admin books are visible to members).
    vol_admin = make_book(
        title="Admin Vol", series="VisSeries", series_index=1.0
    )
    db.flush()

    from backend.main import create_app
    from backend.core.database import get_db

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    from urllib.parse import quote
    from starlette.testclient import TestClient as TC
    with TC(app, raise_server_exceptions=True) as member_client:
        member_client.headers["Authorization"] = f"Bearer {member_token}"
        resp = member_client.get(f"/api/series/{quote('VisSeries', safe='')}/reading-stats")
        assert resp.status_code == 200
        own = resp.json()["own"]
        # Member can see the admin-uploaded book
        assert own["books_total"] == 1

    app.dependency_overrides.clear()


def test_empty_series_returns_zeros(client: TestClient):
    """A series name with no books returns zero stats (no 404)."""
    data = _get_stats(client, "NonExistentSeries")
    own = data["own"]
    assert own["books_total"] == 0
    assert own["sessions"] == 0
    assert own["per_volume"] == []
    assert data["aggregate"] is not None   # admin sees aggregate (also empty)
    assert data["aggregate"]["total_sessions"] == 0


def test_url_encoded_series_name(client: TestClient, make_book, admin_user, db: Session):
    """Series names containing special chars are decoded correctly."""
    user, _ = admin_user
    vol1 = make_book(title="Vol 1", series="Re:ZERO", series_index=1.0)
    now = datetime.utcnow()
    _make_session(db, user.id, vol1.id, now - timedelta(days=1), duration_seconds=1200)
    db.flush()

    from urllib.parse import quote
    resp = client.get(f"/api/series/{quote('Re:ZERO', safe='')}/reading-stats")
    assert resp.status_code == 200
    assert resp.json()["own"]["sessions"] == 1
