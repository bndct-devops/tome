"""Tests for new stats fields added in the stats-revamp phase."""
import pytest
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import ReadingSession
from backend.models.book import Book, BookFile
from backend.models.library import Library, BookType


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_stats(client: TestClient, days: int = 0) -> dict:
    resp = client.get(f"/api/stats?days={days}&tz_offset=0")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_session(
    db: Session,
    user_id: int,
    book_id: int,
    started_at: datetime,
    duration_seconds: int = 300,
    pages_turned: int = 10,
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


def _make_status(
    db: Session,
    user_id: int,
    book_id: int,
    status: str = "read",
    updated_at: datetime | None = None,
) -> UserBookStatus:
    ubs = UserBookStatus(
        user_id=user_id,
        book_id=book_id,
        status=status,
        updated_at=updated_at or datetime.utcnow(),
    )
    db.add(ubs)
    db.flush()
    return ubs


# ── series completion ─────────────────────────────────────────────────────────

def test_series_completion(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    b1 = make_book(title="Foo Vol 1", series="Foo", series_index=1)
    b2 = make_book(title="Foo Vol 2", series="Foo", series_index=2)
    b3 = make_book(title="Foo Vol 3", series="Foo", series_index=3)

    _make_status(db, user.id, b1.id, "read")
    _make_status(db, user.id, b2.id, "read")
    _make_status(db, user.id, b3.id, "reading")

    data = _get_stats(client)
    completions = data["series_completion"]
    foo = next((c for c in completions if c["series"] == "Foo"), None)

    assert foo is not None, "Foo series should appear in series_completion"
    assert foo["total"] == 3
    assert foo["read"] == 2
    assert foo["reading"] == 1
    assert foo["pct"] == pytest.approx(66.7, abs=0.1)


def test_series_completion_excludes_untouched(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    make_book(title="Untouched Vol 1", series="NeverRead", series_index=1)
    make_book(title="Untouched Vol 2", series="NeverRead", series_index=2)

    data = _get_stats(client)
    completions = data["series_completion"]
    names = [c["series"] for c in completions]
    assert "NeverRead" not in names, "Series with no user interaction must not appear"


# ── author affinity ───────────────────────────────────────────────────────────

def test_author_affinity(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    b1 = make_book(title="Book A", author="Shared Author")
    b2 = make_book(title="Book B", author="Shared Author")
    b3 = make_book(title="Book C", author="Other Author")

    now = datetime.utcnow()
    _make_session(db, user.id, b1.id, now - timedelta(hours=2), duration_seconds=600)
    _make_session(db, user.id, b2.id, now - timedelta(hours=1), duration_seconds=900)
    _make_session(db, user.id, b3.id, now - timedelta(hours=3), duration_seconds=100)

    data = _get_stats(client)
    affinity = data["author_affinity"]

    shared = next((a for a in affinity if a["author"] == "Shared Author"), None)
    assert shared is not None
    assert shared["seconds"] == 1500
    assert shared["book_count"] == 2

    # Shared Author should rank above Other Author
    authors = [a["author"] for a in affinity]
    assert authors.index("Shared Author") < authors.index("Other Author")


# ── completion rate ───────────────────────────────────────────────────────────

def test_completion_rate(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    books = [make_book(title=f"CR Book {i}") for i in range(4)]
    _make_status(db, user.id, books[0].id, "read")
    _make_status(db, user.id, books[1].id, "read")
    _make_status(db, user.id, books[2].id, "reading")
    _make_status(db, user.id, books[3].id, "reading")

    data = _get_stats(client)
    cr = data["completion_rate"]

    assert cr["started"] == 4
    assert cr["finished"] == 2
    assert cr["pct"] == pytest.approx(50.0)


def test_completion_rate_zero_when_no_activity(client: TestClient, db: Session):
    data = _get_stats(client)
    cr = data["completion_rate"]
    assert cr["started"] == 0
    assert cr["finished"] == 0
    assert cr["pct"] == 0.0


# ── pace by format ────────────────────────────────────────────────────────────

def test_pace_by_format(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    epub_book = make_book(title="EPUB Book", file_format="epub")
    pdf_book = make_book(title="PDF Book", file_format="pdf")

    now = datetime.utcnow()
    # EPUB: 5 min / 10 pages = 2.0 pages/min
    _make_session(db, user.id, epub_book.id, now - timedelta(hours=2), duration_seconds=300, pages_turned=10)
    # PDF: 10 min / 10 pages = 1.0 pages/min
    _make_session(db, user.id, pdf_book.id, now - timedelta(hours=1), duration_seconds=600, pages_turned=10)

    data = _get_stats(client)
    pbf = {r["format"]: r for r in data["pace_by_format"]}

    assert "epub" in pbf
    assert pbf["epub"]["pages_per_min"] == pytest.approx(2.0, abs=0.01)
    assert "pdf" in pbf
    assert pbf["pdf"]["pages_per_min"] == pytest.approx(1.0, abs=0.01)


# ── hour x dow heatmap ────────────────────────────────────────────────────────

def test_hour_dow_heatmap_structure(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    book = make_book(title="Heatmap Book")
    # Seed a session at a fixed UTC time: Wednesday 2026-01-07 14:00 UTC
    # SQLite strftime('%w',...) gives 0=Sun so Wednesday=3
    fixed_dt = datetime(2026, 1, 7, 14, 0, 0)
    _make_session(db, user.id, book.id, fixed_dt, duration_seconds=600)

    data = _get_stats(client)
    heatmap = data["hour_dow_heatmap"]

    assert len(heatmap) == 168, "Heatmap must have 168 cells (7 days * 24 hours)"
    assert all("dow" in c and "hour" in c and "seconds" in c and "sessions" in c for c in heatmap)

    cell = next((c for c in heatmap if c["dow"] == 3 and c["hour"] == 14), None)
    assert cell is not None
    assert cell["seconds"] == 600
    assert cell["sessions"] == 1

    empty_cells = [c for c in heatmap if not (c["dow"] == 3 and c["hour"] == 14)]
    assert all(c["seconds"] == 0 for c in empty_cells)


# ── library growth ────────────────────────────────────────────────────────────

def test_library_growth(client: TestClient, db: Session, make_book, admin_user):
    user, _token = admin_user

    # Books added at different times
    b1 = make_book(title="Old Book")
    b2 = make_book(title="Newer Book")

    now = datetime.utcnow()
    b1.added_at = now - timedelta(days=60)
    b2.added_at = now - timedelta(days=5)
    db.flush()

    data = _get_stats(client)
    growth = data["library_growth"]

    assert len(growth) == 24, "Library growth must span 24 months"
    assert all("month" in g and "total" in g for g in growth)

    # Cumulative total at end should be at least 2
    assert growth[-1]["total"] >= 2


# ── visibility — member cannot see private admin books in series_completion ───

def test_series_completion_visibility(client: TestClient, db: Session, make_book, admin_user):
    _admin, _token = admin_user

    # Admin creates a book in a series — it belongs to admin only (no public library)
    admin_series_book = make_book(title="Admin Series Vol 1", series="PrivateSeries", series_index=1)
    _make_status(db, _admin.id, admin_series_book.id, "read")

    # Create a member user with no access to the admin's books
    member = User(
        username="member_vis",
        email="member_vis@example.com",
        hashed_password=hash_password("pass1234"),
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

    resp = client.get(
        "/api/stats?days=0&tz_offset=0",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Member has no status on this book, so the series should not appear for them
    names = [c["series"] for c in data["series_completion"]]
    assert "PrivateSeries" not in names, "Member must not see admin-only series"
