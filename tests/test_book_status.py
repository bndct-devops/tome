"""Tests for the per-user book reading-status endpoints.

Endpoints under test:
    GET  /api/books/{book_id}/status
    PUT  /api/books/{book_id}/status
"""
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission


# ── helpers ───────────────────────────────────────────────────────────────────

def _put_status(client: TestClient, book_id: int, payload: dict) -> dict:
    resp = client.put(f"/api/books/{book_id}/status", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_status(client: TestClient, book_id: int) -> dict:
    resp = client.get(f"/api/books/{book_id}/status")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_set_status_reading(client: TestClient, make_book):
    book = make_book(title="Reading Test")

    _put_status(client, book.id, {"status": "reading"})
    data = _get_status(client, book.id)

    assert data["status"] == "reading"
    assert data["book_id"] == book.id


def test_set_status_read(client: TestClient, make_book):
    book = make_book(title="Read Test")

    data = _put_status(client, book.id, {"status": "read", "progress_pct": 1.0})

    assert data["status"] == "read"
    assert data["progress_pct"] == pytest.approx(1.0)


def test_progress_pct_stored_as_fraction(client: TestClient, make_book):
    """Progress must be stored and returned on the 0-1 scale, not 0-100."""
    book = make_book(title="Progress Fraction Test")

    _put_status(client, book.id, {"status": "reading", "progress_pct": 0.62})
    data = _get_status(client, book.id)

    assert data["progress_pct"] == pytest.approx(0.62)


def test_partial_update_preserves_fields(client: TestClient, make_book):
    """PUT with only status must not wipe out progress_pct or cfi."""
    book = make_book(title="Partial Update Test")
    cfi = "epubcfi(/6/4[chap01ref]!/4[body01]/10[para05]/2/1:3)"

    # Full initial write
    _put_status(client, book.id, {
        "status": "reading",
        "progress_pct": 0.45,
        "cfi": cfi,
    })

    # Partial update — only status, no progress_pct, no cfi
    _put_status(client, book.id, {"status": "reading"})

    data = _get_status(client, book.id)
    assert data["progress_pct"] == pytest.approx(0.45), \
        "progress_pct should not be overwritten when absent from the update payload"
    assert data["cfi"] == cfi, \
        "cfi should not be overwritten when absent from the update payload"


def test_cfi_preserved_on_status_only_update(client: TestClient, make_book):
    """CFI string must survive a status-only PUT."""
    book = make_book(title="CFI Preserve Test")
    cfi = "epubcfi(/6/8[chap03ref]!/4[body03]/2/1:0)"

    _put_status(client, book.id, {"status": "reading", "cfi": cfi})
    _put_status(client, book.id, {"status": "read"})

    data = _get_status(client, book.id)
    assert data["cfi"] == cfi


def test_unread_clears_progress_and_cfi(client: TestClient, make_book):
    """Setting status to 'unread' must wipe progress_pct and cfi."""
    book = make_book(title="Unread Clears Progress")

    _put_status(client, book.id, {
        "status": "reading",
        "progress_pct": 0.64,
        "cfi": "comic:42",
    })

    # Mark as unread
    data = _put_status(client, book.id, {"status": "unread"})
    assert data["status"] == "unread"
    assert data["progress_pct"] is None, "progress_pct must be cleared on unread"
    assert data["cfi"] is None, "cfi must be cleared on unread"

    # Verify via GET
    data = _get_status(client, book.id)
    assert data["progress_pct"] is None
    assert data["cfi"] is None


def test_unread_then_reading_starts_fresh(client: TestClient, make_book):
    """After marking unread, re-reading should start with no saved position."""
    book = make_book(title="Unread Fresh Start")

    # Read to page 64
    _put_status(client, book.id, {
        "status": "reading",
        "progress_pct": 0.34,
        "cfi": "comic:64",
    })

    # Mark unread
    _put_status(client, book.id, {"status": "unread"})

    # Start reading again — status only, no progress
    _put_status(client, book.id, {"status": "reading"})

    data = _get_status(client, book.id)
    assert data["status"] == "reading"
    assert data["progress_pct"] is None, \
        "progress should not resurface after unread"
    assert data["cfi"] is None, \
        "cfi should not resurface after unread"


def test_read_preserves_progress_and_cfi(client: TestClient, make_book):
    """Setting status to 'read' (not 'unread') must NOT clear progress/cfi."""
    book = make_book(title="Read Preserves Progress")
    cfi = "epubcfi(/6/4!/4/2/1:3)"

    _put_status(client, book.id, {
        "status": "reading",
        "progress_pct": 0.95,
        "cfi": cfi,
    })

    # Mark as read (no progress_pct/cfi in payload)
    _put_status(client, book.id, {"status": "read"})

    data = _get_status(client, book.id)
    assert data["status"] == "read"
    assert data["progress_pct"] == pytest.approx(0.95), \
        "progress_pct should survive a read status update"
    assert data["cfi"] == cfi, \
        "cfi should survive a read status update"


def test_comic_cfi_format(client: TestClient, make_book):
    """Comic page positions stored as 'comic:{page}' must round-trip."""
    book = make_book(title="Comic CFI Test", file_format="cbz")

    _put_status(client, book.id, {
        "status": "reading",
        "progress_pct": 0.5,
        "cfi": "comic:93",
    })

    data = _get_status(client, book.id)
    assert data["cfi"] == "comic:93"
    assert data["progress_pct"] == pytest.approx(0.5)


def test_default_status_unread(client: TestClient, make_book):
    """GET for a book with no stored status should return status='unread'."""
    book = make_book(title="Default Status Test")

    data = _get_status(client, book.id)

    assert data["status"] == "unread"
    assert data["book_id"] == book.id
    assert data["progress_pct"] is None
    assert data["cfi"] is None


def test_status_per_user(
    db: Session,
    client: TestClient,
    make_book,
    admin_user,
):
    """User A and user B must have independent status rows for the same book."""
    book = make_book(title="Per-User Status Test")

    # Set status as the admin user (the default client user)
    _put_status(client, book.id, {"status": "read", "progress_pct": 1.0})

    # Create a second user
    user_b = User(
        username="user_b",
        email="userb@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=False,
        must_change_password=False,
    )
    db.add(user_b)
    db.flush()
    db.add(UserPermission(user_id=user_b.id))
    db.flush()

    token_b = create_access_token(subject=user_b.id)

    # GET status as user B — should default to "unread"
    resp = client.get(
        f"/api/books/{book.id}/status",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200
    data_b = resp.json()
    assert data_b["status"] == "unread", \
        "User B should have a fresh unread status, not inherit User A's 'read'"


def test_mark_read_at_full_progress(client: TestClient, make_book):
    """PUT status='read' with progress_pct=1.0 should persist both fields."""
    book = make_book(title="Mark Read Full Progress Test")

    data = _put_status(client, book.id, {"status": "read", "progress_pct": 1.0})

    assert data["status"] == "read"
    assert data["progress_pct"] == pytest.approx(1.0)

    # Confirm via GET
    data = _get_status(client, book.id)
    assert data["status"] == "read"
    assert data["progress_pct"] == pytest.approx(1.0)


# ── want_to_read ──────────────────────────────────────────────────────────────

def test_want_to_read_accepted_and_filterable(client: TestClient, make_book):
    """want_to_read is a valid fifth status and drives the list filter."""
    queued = make_book(title="Queued Book")
    plain = make_book(title="Plain Unread Book")

    data = _put_status(client, queued.id, {"status": "want_to_read"})
    assert data["status"] == "want_to_read"

    resp = client.get("/api/books", params={"reading_status": "want_to_read"})
    assert resp.status_code == 200
    ids = [b["id"] for b in resp.json()]
    assert queued.id in ids and plain.id not in ids

    # A queued book is no longer "unread" for the filter
    resp = client.get("/api/books", params={"reading_status": "unread"})
    ids = [b["id"] for b in resp.json()]
    assert queued.id not in ids and plain.id in ids


def test_want_to_read_keeps_progress(client: TestClient, make_book):
    """Unlike unread, queueing a sampled book keeps its position (like shelved)."""
    book = make_book(title="Sampled Then Queued")
    _put_status(client, book.id, {"status": "reading", "progress_pct": 0.1, "cfi": "x"})

    data = _put_status(client, book.id, {"status": "want_to_read"})
    assert data["progress_pct"] == pytest.approx(0.1)
    assert data["cfi"] == "x"


def test_invalid_status_rejected(client: TestClient, make_book):
    book = make_book(title="Bad Status")
    resp = client.put(f"/api/books/{book.id}/status", json={"status": "wishlisted"})
    assert resp.status_code == 400


def test_want_to_read_promotes_to_reading_on_progress(db: Session, client: TestClient, admin_user, make_book):
    """First real progress self-promotes a queued book (device/web sync path)."""
    from backend.services.book_progress import apply_progress_to_status

    user, _ = admin_user
    book = make_book(title="Queued Promotion")
    _put_status(client, book.id, {"status": "want_to_read"})

    row = apply_progress_to_status(db, user_id=user.id, book_id=book.id, pct=0.05)
    db.flush()
    assert row.status == "reading"


def test_want_to_read_finishes_straight_to_read(db: Session, client: TestClient, admin_user, make_book):
    from backend.services.book_progress import apply_progress_to_status

    user, _ = admin_user
    book = make_book(title="Queued Straight To Read")
    _put_status(client, book.id, {"status": "want_to_read"})

    row = apply_progress_to_status(db, user_id=user.id, book_id=book.id, pct=1.0)
    db.flush()
    assert row.status == "read"
    assert row.finished_at is not None
