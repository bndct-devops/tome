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
    device: str | None = None,
    progress_end: float | None = None,
) -> ReadingSession:
    s = ReadingSession(
        user_id=user_id,
        book_id=book_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        pages_turned=pages_turned,
        device=device,
        progress_end=progress_end,
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


# ── Reading Log enrichment (by_source / momentum / finished_at / journey) ──────

def test_by_source_split(client: TestClient, make_book, admin_user, db: Session):
    """by_source breaks reading time down per device, ordered by seconds desc."""
    user, _ = admin_user
    book = make_book(title="Source Split Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=2), duration_seconds=600, device="web-reader")
    _make_session(db, user.id, book.id, now - timedelta(days=2), duration_seconds=1800, device="Kindle")
    db.flush()

    own = _get_stats(client, book.id)["own"]
    by_source = {s["device"]: s for s in own["by_source"]}
    assert by_source["Kindle"]["seconds"] == 1800
    assert by_source["web-reader"]["seconds"] == 600
    # ordered most-read first
    assert own["by_source"][0]["device"] == "Kindle"


def test_momentum_recent_vs_prior(client: TestClient, make_book, admin_user, db: Session):
    """momentum compares the last 7 days against the 7 before."""
    user, _ = admin_user
    book = make_book(title="Momentum Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=2), duration_seconds=1200)  # recent week
    _make_session(db, user.id, book.id, now - timedelta(days=9), duration_seconds=600)   # prior week
    db.flush()

    m = _get_stats(client, book.id)["own"]["momentum"]
    assert m is not None
    assert m["recent_seconds"] == 1200
    assert m["prior_seconds"] == 600
    assert m["direction"] == "up"
    assert m["delta_pct"] == 100


def test_finished_at_set_when_read(client: TestClient, make_book, admin_user, db: Session):
    """finished_at is populated once the book is marked read."""
    user, _ = admin_user
    book = make_book(title="Finished Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=600)
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.flush()

    own = _get_stats(client, book.id)["own"]
    assert own["status"] == "read"
    assert own["finished_at"] is not None


def test_journey_progress_is_monotonic(client: TestClient, make_book, admin_user, db: Session):
    """Each timeline day carries a non-decreasing progress_pct for the journey arc."""
    user, _ = admin_user
    book = make_book(title="Journey Book")
    now = datetime.utcnow()
    _make_session(db, user.id, book.id, now - timedelta(days=5), duration_seconds=600, progress_end=0.2)
    _make_session(db, user.id, book.id, now - timedelta(days=2), duration_seconds=600, progress_end=0.6)
    db.flush()

    own = _get_stats(client, book.id)["own"]
    progs = [r["progress_pct"] for r in own["session_timeline"] if r["progress_pct"] is not None]
    assert progs == sorted(progs)           # never dips
    assert progs[-1] == pytest.approx(60.0)


# ── Manual session logging (POST /books/{id}/sessions) ─────────────────────────

def test_manual_session_logging(client: TestClient, make_book, admin_user, db: Session):
    """A manual session is recorded as device='manual' and advances progress."""
    user, _ = admin_user
    book = make_book(title="Manual Log Book")

    resp = client.post(f"/api/books/{book.id}/sessions", json={"duration_minutes": 30, "end_progress": 0.5})
    assert resp.status_code == 201, resp.text

    own = resp.json()["own"]
    assert own["total_seconds"] == 1800
    assert own["status"] == "reading"
    assert own["progress"] == pytest.approx(0.5)
    assert any(s["device"] == "manual" for s in own["by_source"])


def test_manual_session_rejects_nonpositive_duration(client: TestClient, make_book):
    book = make_book(title="Bad Session Book")
    resp = client.post(f"/api/books/{book.id}/sessions", json={"duration_minutes": 0})
    assert resp.status_code == 422


def test_aggregate_includes_page_stats(client: TestClient, make_book, admin_user, db: Session):
    """A book read only via imported KOReader page-stats still counts in the admin aggregate."""
    from backend.models.ko_stats import PageStat
    user, _ = admin_user
    book = make_book(title="Device Only Book")
    base = 1_700_000_000
    db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=100,
                    start_time=base, duration_seconds=60, device="Kindle"))
    db.add(PageStat(user_id=user.id, book_id=book.id, page=2, total_pages=100,
                    start_time=base + 90_000, duration_seconds=60, device="Kindle"))  # +1 day
    db.flush()

    agg = _get_stats(client, book.id)["aggregate"]
    assert agg is not None
    assert agg["total_seconds"] == 120
    assert agg["distinct_readers"] == 1
    assert agg["total_sessions"] == 2          # two distinct reading days, not zero


def test_manual_session_completion_is_sticky(client: TestClient, make_book, admin_user, db: Session):
    """A manual session never un-finishes an already-read book."""
    user, _ = admin_user
    book = make_book(title="Sticky Book")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.flush()

    resp = client.post(f"/api/books/{book.id}/sessions", json={"duration_minutes": 10, "end_progress": 0.3})
    assert resp.status_code == 201
    assert resp.json()["own"]["status"] == "read"


# ── Progress = synced position, not page coverage (device-book regression) ─────

def _page_run(db, user_id, book_id, *, pages, total_pages, device="Kindle", base=1_700_000_000):
    """Add a 1-minute dwell row for each page in `pages` (distinct start_times)."""
    from backend.models.ko_stats import PageStat
    for i, pg in enumerate(pages):
        db.add(PageStat(user_id=user_id, book_id=book_id, page=pg, total_pages=total_pages,
                        start_time=base + i * 60, duration_seconds=60, device=device))
    db.flush()


def test_progress_uses_synced_position_not_coverage(client: TestClient, make_book, admin_user, db: Session):
    """Regression: a device book showed 11% (coverage) while the reader was at 35% (position)."""
    user, _ = admin_user
    book = make_book(title="Position Book")
    # dwelled on only the first 123 pages of a 1008-page book…
    _page_run(db, user.id, book.id, pages=range(1, 124), total_pages=1008)
    # …but the reader synced its position at 34.8%.
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.348))
    db.flush()

    own = _get_stats(client, book.id)["own"]
    assert own["progress"] == pytest.approx(0.348)             # synced position, not 0.122 coverage
    # Device book: no progress line — per-day position can't come from dwell.
    assert all(d["progress_pct"] is None for d in own["session_timeline"])


def test_progress_finished_device_book_is_full(client: TestClient, make_book, admin_user, db: Session):
    """A finished book with no synced position still reads 100%, not coverage."""
    user, _ = admin_user
    book = make_book(title="Finished Device Book")
    # read to the end but missed a few pages → distinct < total
    _page_run(db, user.id, book.id, pages=[p for p in range(1, 477) if p % 50 != 0], total_pages=476)
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=0.0))
    db.flush()

    own = _get_stats(client, book.id)["own"]
    assert own["status"] == "read"
    assert own["progress"] == pytest.approx(1.0)


def test_progress_fallback_uses_furthest_page_not_distinct(client: TestClient, make_book, admin_user, db: Session):
    """No synced position: progress = furthest page reached, not distinct-page count."""
    user, _ = admin_user
    book = make_book(title="Skimmed Book")
    # dwelled on 50 pages but reached page 500 of 1000
    pages = list(range(1, 41)) + list(range(491, 501))        # 50 distinct, max 500
    _page_run(db, user.id, book.id, pages=pages, total_pages=1000)
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.0))
    db.flush()

    own = _get_stats(client, book.id)["own"]
    assert own["progress"] == pytest.approx(0.5)              # 500/1000, not 50/1000
