"""Send-to-KOReader inbox (beta): web enqueue + plugin inbox pull/deliver.

Covers the feature flag gate, enqueue + dedup + visibility, the plugin-facing
inbox listing, and the delivered transition.
"""
import uuid

import pytest

from backend.core.config import settings
from backend.core.security import create_access_token, hash_password
from backend.models.user import User
from backend.models.tome_sync import ApiKey


def _user(db, admin: bool = True) -> User:
    name = "u" + uuid.uuid4().hex[:8]
    u = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("pw123456"),
        is_active=True,
        is_admin=admin,
        role="admin" if admin else "member",
        must_change_password=False,
    )
    db.add(u)
    db.flush()
    return u


def _bearer(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def _api_key(db, user_id: int) -> dict:
    plaintext = ApiKey.generate()
    db.add(ApiKey(
        user_id=user_id,
        key_hash=ApiKey.hash_key(plaintext),
        key_prefix=plaintext[:11],
        label="test",
    ))
    db.flush()
    return {"Authorization": f"Bearer {plaintext}"}


@pytest.fixture()
def koreader_on(monkeypatch):
    monkeypatch.setattr(settings, "send_to_koreader", True)
    yield


# ── Feature gate ──────────────────────────────────────────────────────────────

def test_enqueue_404_when_disabled(client, db, make_book):
    assert settings.send_to_koreader is False  # default off (beta)
    user = _user(db)
    book = make_book(title="Gated")
    r = client.post("/api/send-to-device/koreader",
                    json={"book_ids": [book.id]}, headers=_bearer(user))
    assert r.status_code == 404


def test_inbox_404_when_disabled(client, db):
    user = _user(db)
    r = client.get("/api/tome-sync/inbox", headers=_api_key(db, user.id))
    assert r.status_code == 404


# ── Enqueue + inbox round-trip ────────────────────────────────────────────────

def test_enqueue_then_inbox_lists_items(client, db, make_book, koreader_on):
    user = _user(db)
    b1 = make_book(title="Solo Title", author="A. Writer")
    b2 = make_book(title="Vol One", author="B. Writer", series="A Series", series_index=1.0)

    r = client.post("/api/send-to-device/koreader",
                    json={"book_ids": [b1.id, b2.id]}, headers=_bearer(user))
    assert r.status_code == 200
    assert r.json() == {"queued": 2, "skipped": 0}

    inbox = client.get("/api/tome-sync/inbox", headers=_api_key(db, user.id)).json()
    assert inbox["count"] == 2
    by_book = {it["book_id"]: it for it in inbox["items"]}
    assert set(by_book) == {b1.id, b2.id}

    series_item = by_book[b2.id]
    assert series_item["series"] == "A Series"
    assert series_item["series_index"] == 1.0
    assert series_item["author"] == "B. Writer"
    assert series_item["book_type"] == "book"  # no book_type set → default
    assert series_item["files"] and series_item["files"][0]["format"] == "epub"
    assert series_item["pinned_file_id"] == b2.files[0].id

    assert by_book[b1.id]["series"] is None


def test_enqueue_dedupes_pending(client, db, make_book, koreader_on):
    user = _user(db)
    book = make_book(title="Dupe Me")
    first = client.post("/api/send-to-device/koreader",
                        json={"book_ids": [book.id]}, headers=_bearer(user))
    assert first.json() == {"queued": 1, "skipped": 0}
    second = client.post("/api/send-to-device/koreader",
                         json={"book_ids": [book.id]}, headers=_bearer(user))
    assert second.json() == {"queued": 0, "skipped": 1}

    inbox = client.get("/api/tome-sync/inbox", headers=_api_key(db, user.id)).json()
    assert inbox["count"] == 1


def test_enqueue_skips_missing_book(client, db, make_book, koreader_on):
    user = _user(db)
    r = client.post("/api/send-to-device/koreader",
                    json={"book_ids": [999999]}, headers=_bearer(user))
    assert r.json() == {"queued": 0, "skipped": 1}


# ── Delivery ──────────────────────────────────────────────────────────────────

def test_delivered_removes_from_inbox(client, db, make_book, koreader_on):
    user = _user(db)
    book = make_book(title="Deliver Me")
    client.post("/api/send-to-device/koreader",
                json={"book_ids": [book.id]}, headers=_bearer(user))
    api = _api_key(db, user.id)

    inbox = client.get("/api/tome-sync/inbox", headers=api).json()
    assert inbox["count"] == 1
    item_id = inbox["items"][0]["id"]

    d = client.post(f"/api/tome-sync/inbox/{item_id}/delivered", headers=api)
    assert d.status_code == 200 and d.json()["ok"] is True

    after = client.get("/api/tome-sync/inbox", headers=api).json()
    assert after["count"] == 0

    # Idempotent: marking again still succeeds.
    again = client.post(f"/api/tome-sync/inbox/{item_id}/delivered", headers=api)
    assert again.status_code == 200


def test_delivered_404_for_other_users_item(client, db, make_book, koreader_on):
    owner = _user(db)
    book = make_book(title="Not Yours")
    client.post("/api/send-to-device/koreader",
                json={"book_ids": [book.id]}, headers=_bearer(owner))
    item_id = client.get("/api/tome-sync/inbox", headers=_api_key(db, owner.id)).json()["items"][0]["id"]

    stranger = _user(db)
    r = client.post(f"/api/tome-sync/inbox/{item_id}/delivered", headers=_api_key(db, stranger.id))
    assert r.status_code == 404
