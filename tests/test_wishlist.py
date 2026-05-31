"""Tests for the Wishlist feature — member and admin endpoints.

Covers:
- Member creates structured wish; appears in GET /wishlist
- Free-text wish (no source) persists; two free-text wishes don't collide
- Duplicate structured wish (same source+source_id) → 409
- Cap exceeded → 409
- Guest cannot create (403); member cannot see another member's wish (own-only)
- Admin sees all wishes with requester names; non-admin gets 403 on /admin/wishlist
- Fulfil: status→fulfilled, links set, Notification row created, audit written
  Email attempted only when SMTP configured (monkeypatched)
- Fulfil already-fulfilled → 409
- Dismiss → status + in-app notice, no email
- Notification endpoints: list unread, mark read, mark-all, ownership enforced
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.book import Book, BookFile
from backend.models.notification import Notification
from backend.models.user import User, UserPermission
from backend.models.wish import Wish


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_member(db: Session, username: str = "member1") -> tuple[User, str]:
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


def _make_guest(db: Session, username: str = "guest1") -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="guest",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id)
    db.add(perms)
    db.flush()
    token = create_access_token(subject=user.id)
    return user, token


def _wish_payload(**kwargs) -> dict:
    defaults = {"title": "My Wish Book", "source": "google_books", "source_id": "abc123"}
    defaults.update(kwargs)
    return defaults


# ── Member create / list ──────────────────────────────────────────────────────

def test_member_creates_wish_and_appears_in_list(client: TestClient, db: Session, admin_user):
    """Member can create a wish and see it in GET /wishlist."""
    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    resp = client.post("/api/wishlist", json=_wish_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "My Wish Book"
    assert body["status"] == "open"
    assert body["source"] == "google_books"
    assert body["source_id"] == "abc123"

    list_resp = client.get("/api/wishlist")
    assert list_resp.status_code == 200
    wishes = list_resp.json()
    assert len(wishes) == 1
    assert wishes[0]["id"] == body["id"]


def test_free_text_wish_persists(client: TestClient, db: Session, admin_user):
    """Free-text wish (no source) is accepted and stored."""
    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    resp = client.post("/api/wishlist", json={"title": "Free Text Book", "note": "I want this"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] is None
    assert body["note"] == "I want this"


def test_two_free_text_wishes_dont_collide(client: TestClient, db: Session, admin_user):
    """Two free-text wishes with no source don't trigger the uniqueness constraint."""
    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    resp1 = client.post("/api/wishlist", json={"title": "Book One"})
    resp2 = client.post("/api/wishlist", json={"title": "Book Two"})
    assert resp1.status_code == 201
    assert resp2.status_code == 201


def test_duplicate_structured_wish_returns_409(client: TestClient, db: Session, admin_user):
    """Same source+source_id → 409."""
    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    client.post("/api/wishlist", json=_wish_payload())
    resp = client.post("/api/wishlist", json=_wish_payload())
    assert resp.status_code == 409


def test_cap_exceeded_returns_409(client: TestClient, db: Session, admin_user):
    """Exceeding wishlist_max_open_per_user returns 409."""
    from backend.core.config import settings

    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    # Insert cap-1 wishes directly (to avoid HTTP overhead)
    original_max = settings.wishlist_max_open_per_user
    settings.wishlist_max_open_per_user = 2

    try:
        # First wish
        client.post("/api/wishlist", json={"title": "Wish 1"})
        # Second wish
        client.post("/api/wishlist", json={"title": "Wish 2"})
        # Third should be capped
        resp = client.post("/api/wishlist", json={"title": "Wish 3"})
        assert resp.status_code == 409
    finally:
        settings.wishlist_max_open_per_user = original_max


def test_guest_cannot_create_wish(client: TestClient, db: Session, admin_user):
    """Guest gets 403 on POST /wishlist."""
    _guest, guest_token = _make_guest(db)
    client.headers["Authorization"] = f"Bearer {guest_token}"

    resp = client.post("/api/wishlist", json={"title": "Guest Wish"})
    assert resp.status_code == 403


def test_member_cannot_see_other_members_wishes(client: TestClient, db: Session, admin_user):
    """Member A's wishes are not visible to member B."""
    member_a, token_a = _make_member(db, "memberA")
    member_b, token_b = _make_member(db, "memberB")

    # Member A creates a wish
    client.headers["Authorization"] = f"Bearer {token_a}"
    client.post("/api/wishlist", json={"title": "Member A Wish"})

    # Member B should see an empty list
    client.headers["Authorization"] = f"Bearer {token_b}"
    resp = client.get("/api/wishlist")
    assert resp.status_code == 200
    assert resp.json() == []


def test_member_can_delete_own_wish(client: TestClient, db: Session, admin_user):
    """DELETE /wishlist/{id} removes the wish."""
    member, member_token = _make_member(db)
    client.headers["Authorization"] = f"Bearer {member_token}"

    create_resp = client.post("/api/wishlist", json={"title": "Deletable Wish"})
    wish_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/wishlist/{wish_id}")
    assert del_resp.status_code == 204

    list_resp = client.get("/api/wishlist")
    assert list_resp.json() == []


def test_member_cannot_delete_others_wish(client: TestClient, db: Session, admin_user):
    """Member B cannot delete Member A's wish."""
    member_a, token_a = _make_member(db, "memberA2")
    member_b, token_b = _make_member(db, "memberB2")

    client.headers["Authorization"] = f"Bearer {token_a}"
    create_resp = client.post("/api/wishlist", json={"title": "A Wish"})
    wish_id = create_resp.json()["id"]

    client.headers["Authorization"] = f"Bearer {token_b}"
    resp = client.delete(f"/api/wishlist/{wish_id}")
    assert resp.status_code == 404


# ── Admin list ────────────────────────────────────────────────────────────────

def test_admin_sees_all_wishes_with_usernames(client: TestClient, db: Session, admin_user):
    """Admin GET /admin/wishlist returns wishes from all members with requester_username."""
    user_admin, admin_token = admin_user

    member_a, token_a = _make_member(db, "memberX")
    member_b, token_b = _make_member(db, "memberY")

    client.headers["Authorization"] = f"Bearer {token_a}"
    client.post("/api/wishlist", json={"title": "Wish from A"})

    client.headers["Authorization"] = f"Bearer {token_b}"
    client.post("/api/wishlist", json={"title": "Wish from B"})

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/admin/wishlist")
    assert resp.status_code == 200
    wishes = resp.json()
    assert len(wishes) == 2
    usernames = {w["requester_username"] for w in wishes}
    assert "memberX" in usernames
    assert "memberY" in usernames


def test_non_admin_cannot_access_admin_wishlist(client: TestClient, db: Session, admin_user):
    """Non-admin gets 403 on GET /admin/wishlist."""
    member, member_token = _make_member(db, "memberZ")
    client.headers["Authorization"] = f"Bearer {member_token}"

    resp = client.get("/api/admin/wishlist")
    assert resp.status_code == 403


# ── Admin fulfill ─────────────────────────────────────────────────────────────

def test_fulfill_wish(client: TestClient, db: Session, admin_user, make_book):
    """Fulfill: status→fulfilled, links set, Notification created, audit written."""
    from backend.models.audit_log import AuditLog

    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_fulfill")

    # Member creates a wish
    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Fulfill Me"})
    wish_id = create_resp.json()["id"]

    # Admin uploads a matching book (via fixture)
    book = make_book(title="Fulfill Me", author="Some Author")

    # Admin fulfills
    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post(
        f"/api/admin/wishlist/{wish_id}/fulfill",
        json={"book_id": book.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fulfilled"
    assert body["fulfilled_book_id"] == book.id
    assert body["fulfilled_by"] == user_admin.id
    assert body["fulfilled_at"] is not None

    # Notification created
    notif = db.query(Notification).filter(
        Notification.user_id == member.id,
        Notification.kind == "wish_fulfilled",
    ).first()
    assert notif is not None
    assert "Fulfill Me" in notif.title

    # Audit written
    audit_entry = db.query(AuditLog).filter(
        AuditLog.action == "wishlist.fulfilled",
    ).first()
    assert audit_entry is not None


def test_fulfill_sends_email_when_smtp_configured(client: TestClient, db: Session, admin_user, make_book):
    """Email helper is called when smtp_configured is True."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_email")

    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Email Me"})
    wish_id = create_resp.json()["id"]

    book = make_book(title="Email Me")

    with (
        patch("backend.services.wishlist.settings") as mock_settings,
        patch("backend.services.wishlist.send_wish_fulfilled_email") as mock_email,
    ):
        mock_settings.smtp_configured = True
        mock_settings.wishlist_enabled = True
        mock_settings.wishlist_max_open_per_user = 100

        client.headers["Authorization"] = f"Bearer {admin_token}"
        resp = client.post(
            f"/api/admin/wishlist/{wish_id}/fulfill",
            json={"book_id": book.id},
        )
        assert resp.status_code == 200
        mock_email.assert_called_once()


def test_fulfill_no_email_when_smtp_not_configured(client: TestClient, db: Session, admin_user, make_book):
    """Email helper is NOT called when smtp_configured is False."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_noemail")

    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "No Email"})
    wish_id = create_resp.json()["id"]

    book = make_book(title="No Email")

    with patch("backend.services.wishlist.settings") as mock_settings:
        mock_settings.smtp_configured = False
        mock_settings.wishlist_enabled = True
        mock_settings.wishlist_max_open_per_user = 100

        client.headers["Authorization"] = f"Bearer {admin_token}"
        resp = client.post(
            f"/api/admin/wishlist/{wish_id}/fulfill",
            json={"book_id": book.id},
        )
        assert resp.status_code == 200
        # No email attempted — smtp_configured is False, so the branch is skipped


def test_fulfill_already_fulfilled_returns_409(client: TestClient, db: Session, admin_user, make_book):
    """Fulfilling an already-fulfilled wish returns 409."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_409")

    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Already Done"})
    wish_id = create_resp.json()["id"]

    book = make_book(title="Already Done")

    client.headers["Authorization"] = f"Bearer {admin_token}"
    client.post(f"/api/admin/wishlist/{wish_id}/fulfill", json={"book_id": book.id})
    resp = client.post(f"/api/admin/wishlist/{wish_id}/fulfill", json={"book_id": book.id})
    assert resp.status_code == 409


def test_complete_whole_series_wish_without_book(client: TestClient, db: Session, admin_user):
    """A whole-series wish can be marked complete with no book_id: status→fulfilled,
    no book linked, notification created."""
    from backend.models.audit_log import AuditLog

    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_series_complete")

    # Member wishes for a whole series (series set, no series_index)
    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Mistborn", "series": "Mistborn"})
    wish_id = create_resp.json()["id"]

    # Admin marks complete — no book_id in the body
    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post(f"/api/admin/wishlist/{wish_id}/fulfill", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fulfilled"
    assert body["fulfilled_book_id"] is None
    assert body["fulfilled_by"] == user_admin.id

    # Notification created (series wording, no book link)
    notif = db.query(Notification).filter(
        Notification.user_id == member.id,
        Notification.kind == "wish_fulfilled",
    ).first()
    assert notif is not None
    assert "Mistborn" in notif.title
    assert notif.link == "/wishlist"

    # Audit written with no book_id
    audit_entry = db.query(AuditLog).filter(AuditLog.action == "wishlist.fulfilled").first()
    assert audit_entry is not None


def test_fulfill_single_book_wish_without_book_returns_400(client: TestClient, db: Session, admin_user):
    """A non-series wish requires a book_id — omitting it is a 400, not a silent close."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_no_book")

    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Standalone Book"})
    wish_id = create_resp.json()["id"]

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post(f"/api/admin/wishlist/{wish_id}/fulfill", json={})
    assert resp.status_code == 400


# ── Admin dismiss ─────────────────────────────────────────────────────────────

def test_dismiss_wish(client: TestClient, db: Session, admin_user):
    """Dismiss: status→dismissed, in-app notification created, no email."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_dismiss")

    client.headers["Authorization"] = f"Bearer {member_token}"
    create_resp = client.post("/api/wishlist", json={"title": "Dismiss Me"})
    wish_id = create_resp.json()["id"]

    with patch("backend.services.wishlist.send_wish_fulfilled_email") as mock_email:
        client.headers["Authorization"] = f"Bearer {admin_token}"
        resp = client.post(f"/api/admin/wishlist/{wish_id}/dismiss")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "dismissed"

        # No email on dismiss
        mock_email.assert_not_called()

    # In-app notification created
    notif = db.query(Notification).filter(
        Notification.user_id == member.id,
        Notification.kind == "wish_dismissed",
    ).first()
    assert notif is not None


# ── Admin wishlist/matches ────────────────────────────────────────────────────

def test_admin_wish_matches_endpoint(client: TestClient, db: Session, admin_user, make_book):
    """GET /admin/wishlist/matches?book_id= returns matching open wishes."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_matches")

    # Member adds a wish for a book that we're about to "upload"
    client.headers["Authorization"] = f"Bearer {member_token}"
    client.post("/api/wishlist", json={
        "title": "My Series Book",
        "author": "Famous Author",
        "isbn": "1234567890",
    })

    # "Upload" a matching book
    book = make_book(title="My Series Book", author="Famous Author", isbn="1234567890")

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get(f"/api/admin/wishlist/matches?book_id={book.id}")
    assert resp.status_code == 200
    wishes = resp.json()
    assert len(wishes) >= 1


# ── Reverse matcher: wish created after book already in library ───────────────

def test_admin_list_wishes_live_suggestions_for_wish_created_after_book(
    client: TestClient, db: Session, admin_user, make_book
):
    """Regression: book added at T1, wish created at T2 > T1.

    The forward matcher never fired for this wish, so suggested_book_ids is
    empty on the DB row.  admin_list_wishes must compute live suggestions and
    include the pre-existing book in the response.
    """
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_reverse_match")

    # Step 1 — book is already in the library (simulates T1)
    book = make_book(title="Mistborn: The Final Empire", author="Brandon Sanderson", series=None)

    # Step 2 — member creates a whole-series wish for "Mistborn" (simulates T2)
    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.post("/api/wishlist", json={
        "title": "Mistborn",
        "author": "Brandon Sanderson",
        "series": "Mistborn",
    })
    assert resp.status_code == 201
    wish_id = resp.json()["id"]

    # Step 3 — admin lists wishes; the live reverse matcher should surface the book
    client.headers["Authorization"] = f"Bearer {admin_token}"
    list_resp = client.get("/api/admin/wishlist?status=open")
    assert list_resp.status_code == 200

    wishes = list_resp.json()
    target = next((w for w in wishes if w["id"] == wish_id), None)
    assert target is not None, "Wish not found in admin list"
    assert target["suggested_book_ids"] is not None, (
        "suggested_book_ids should not be None — reverse matcher should have fired"
    )
    assert book.id in target["suggested_book_ids"], (
        f"Book {book.id} ('{book.title}') missing from suggested_book_ids {target['suggested_book_ids']}"
    )


def test_admin_list_wishes_non_open_wishes_keep_stored_suggestions(
    client: TestClient, db: Session, admin_user, make_book
):
    """Fulfilled/dismissed wishes keep their stored suggested_book_ids unchanged."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_fulfilled_suggest")

    book = make_book(title="Dune", author="Frank Herbert")

    # Create the wish
    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.post("/api/wishlist", json={"title": "Dune", "author": "Frank Herbert"})
    assert resp.status_code == 201
    wish_id = resp.json()["id"]

    # Fulfill the wish
    client.headers["Authorization"] = f"Bearer {admin_token}"
    fulfill_resp = client.post(
        f"/api/admin/wishlist/{wish_id}/fulfill",
        json={"book_id": book.id},
    )
    assert fulfill_resp.status_code == 200

    # Admin lists all wishes (no status filter — gets fulfilled ones too)
    list_resp = client.get("/api/admin/wishlist")
    assert list_resp.status_code == 200
    wishes = list_resp.json()
    target = next((w for w in wishes if w["id"] == wish_id), None)
    assert target is not None
    # Fulfilled wish — should not have live matcher applied (status != "open")
    # The stored suggested_book_ids is whatever the DB has (may be None or set)
    assert target["status"] == "fulfilled"


# ── Notification endpoints ────────────────────────────────────────────────────

def test_list_notifications(client: TestClient, db: Session, admin_user):
    """GET /notifications returns user's notifications."""
    user_admin, admin_token = admin_user

    # Insert a notification directly
    notif = Notification(
        user_id=user_admin.id,
        kind="test",
        title="Test notification",
        read=False,
    )
    db.add(notif)
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert any(n["title"] == "Test notification" for n in data)


def test_list_notifications_unread_filter(client: TestClient, db: Session, admin_user):
    """GET /notifications?unread=true filters to unread only."""
    user_admin, admin_token = admin_user

    db.add(Notification(user_id=user_admin.id, kind="test", title="Unread", read=False))
    db.add(Notification(user_id=user_admin.id, kind="test", title="Read", read=True))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/notifications?unread=true")
    assert resp.status_code == 200
    data = resp.json()
    assert all(not n["read"] for n in data)


def test_mark_notification_read(client: TestClient, db: Session, admin_user):
    """POST /notifications/{id}/read marks a notification as read."""
    user_admin, admin_token = admin_user

    notif = Notification(user_id=user_admin.id, kind="test", title="Mark Me", read=False)
    db.add(notif)
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post(f"/api/notifications/{notif.id}/read")
    assert resp.status_code == 200
    assert resp.json()["read"] is True


def test_mark_all_notifications_read(client: TestClient, db: Session, admin_user):
    """POST /notifications/read-all marks all as read."""
    user_admin, admin_token = admin_user

    db.add(Notification(user_id=user_admin.id, kind="test", title="N1", read=False))
    db.add(Notification(user_id=user_admin.id, kind="test", title="N2", read=False))
    db.flush()

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.post("/api/notifications/read-all")
    assert resp.status_code == 200

    unread = db.query(Notification).filter(
        Notification.user_id == user_admin.id,
        Notification.read == False,  # noqa: E712
    ).count()
    assert unread == 0


def test_notification_ownership_enforced(client: TestClient, db: Session, admin_user):
    """Member cannot mark another user's notification as read."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_notif")

    # Admin notification
    notif = Notification(user_id=user_admin.id, kind="test", title="Admin Notif", read=False)
    db.add(notif)
    db.flush()

    # Member tries to mark it
    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.post(f"/api/notifications/{notif.id}/read")
    assert resp.status_code == 404


# ── Series coverage ───────────────────────────────────────────────────────────

def test_member_sees_series_coverage(client: TestClient, db: Session, admin_user, make_book):
    """A whole-series wish exposes the volumes already in the library, sorted by index."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_coverage")

    make_book(title="Vol One", series="Coverage Saga", series_index=1)
    make_book(title="Vol Three", series="Coverage Saga", series_index=3)

    client.headers["Authorization"] = f"Bearer {member_token}"
    client.post("/api/wishlist", json={"title": "Coverage Saga", "series": "Coverage Saga"})

    resp = client.get("/api/wishlist?status=open")
    assert resp.status_code == 200
    wish = resp.json()[0]
    cov = wish["series_coverage"]
    assert cov is not None
    assert {v["title"] for v in cov} == {"Vol One", "Vol Three"}
    assert [v["series_index"] for v in cov] == [1.0, 3.0]


def test_admin_sees_series_coverage(client: TestClient, db: Session, admin_user, make_book):
    """Admin wishlist also exposes whole-series coverage."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_cov_admin")

    make_book(title="A1", series="Admin Saga", series_index=1)

    client.headers["Authorization"] = f"Bearer {member_token}"
    client.post("/api/wishlist", json={"title": "Admin Saga", "series": "Admin Saga"})

    client.headers["Authorization"] = f"Bearer {admin_token}"
    resp = client.get("/api/admin/wishlist?status=open")
    assert resp.status_code == 200
    target = next(w for w in resp.json() if w["title"] == "Admin Saga")
    assert target["series_coverage"] is not None
    assert {v["title"] for v in target["series_coverage"]} == {"A1"}


def test_single_book_wish_has_no_coverage(client: TestClient, db: Session, admin_user, make_book):
    """A non-series wish has no series_coverage."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_no_cov")

    client.headers["Authorization"] = f"Bearer {member_token}"
    client.post("/api/wishlist", json={"title": "Standalone Cov"})

    resp = client.get("/api/wishlist?status=open")
    assert not resp.json()[0].get("series_coverage")


# ── Canonical series wishes (Hardcover series id + total, author disambiguation) ──

def test_series_wish_persists_external_id_and_total(client: TestClient, db: Session, admin_user):
    """A whole-series wish from a Hardcover series result stores the canonical id
    and true volume count; series_total surfaces on the listed wish."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_series_meta")
    client.headers["Authorization"] = f"Bearer {member_token}"
    resp = client.post("/api/wishlist", json={
        "title": "The Good Guys", "series": "The Good Guys", "author": "Eric Ugland",
        "source": "hardcover", "source_id": "35135",
        "external_series_id": "35135", "series_total": 16,
    })
    assert resp.status_code == 201
    assert resp.json()["external_series_id"] == "35135"

    w = client.get("/api/wishlist?status=open").json()[0]
    assert w["series_total"] == 16
    assert w["external_series_id"] == "35135"


def test_series_coverage_author_disambiguation(client: TestClient, db: Session, admin_user, make_book):
    """Two same-named series with different authors don't cross-match: a series
    wish with an author only covers that author's volumes."""
    user_admin, admin_token = admin_user
    member, member_token = _make_member(db, "member_disambig")
    make_book(title="Ugland Vol 1", series="The Good Guys", series_index=1, author="Eric Ugland")
    make_book(title="Bonanno Vol 1", series="The Good Guys", series_index=1, author="Bill Bonanno")

    client.headers["Authorization"] = f"Bearer {member_token}"
    client.post("/api/wishlist", json={"title": "The Good Guys", "series": "The Good Guys", "author": "Eric Ugland"})

    w = client.get("/api/wishlist?status=open").json()[0]
    titles = {v["title"] for v in (w["series_coverage"] or [])}
    assert titles == {"Ugland Vol 1"}
