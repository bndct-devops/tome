"""Reverse KOSync position bridge tests (issue #175, TOME_KOSYNC_POSITION_BRIDGE).

The KOSync endpoint is a deliberately one-way bridge (#156): third-party
pushes never write TomeSyncPosition. With the flag ON, the PLUGIN's position
pull (GET /api/tome-sync/position/{book_id}) additionally considers the
newest KOSync push for the same book — read-side only. Covered here:

- flag off (the default): behaviour is unchanged, newer KOSync data or not
- flag on: a newer KOSync push is served to the plugin; an older one is not
- flag on: KOSync data alone satisfies the pull (no 404) — the #175 setup,
  where only the third-party client has synced the book so far
- the bridge never writes TomeSyncPosition, and never leaks across users

tome-sync endpoints authenticate by API key only (not JWT) — mirroring
test_reading_progress.py.
"""
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import hash_password
from backend.models.kosync import KOSyncDocumentMap, KOSyncProgress, KOSyncUser
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user import User


MD5 = "ab" * 16  # any 32-hex string (KOReader partial-MD5)


# ── helpers / fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def ts_client(db: Session):
    """Yield (TestClient, user, api_key_headers) with the test db wired in."""
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    user = User(
        username="bridgeuser", email="bridge@example.com",
        hashed_password=hash_password("pass"), is_active=True,
        role="member", must_change_password=False,
    )
    db.add(user)
    db.flush()
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, user, {"Authorization": f"Bearer {plaintext}"}

    app.dependency_overrides.clear()


def _seed_kosync_push(db, tome_user, book, percentage, age_seconds=0,
                      device="crosspoint", document=MD5, progress="/body/DocFragment[8]/body/p[3]"):
    """A linked KOSync account + document map + one progress push, the state a
    real third-party client leaves behind after PUT /api/v1/syncs/progress."""
    acc = db.query(KOSyncUser).filter_by(user_id=tome_user.id).first()
    if acc is None:
        acc = KOSyncUser(username=f"ks-{tome_user.username}", userkey="k" * 32,
                         user_id=tome_user.id)
        db.add(acc)
        db.flush()
    if not db.query(KOSyncDocumentMap).filter_by(
            tome_user_id=tome_user.id, document=document).first():
        db.add(KOSyncDocumentMap(tome_user_id=tome_user.id, document=document,
                                 book_id=book.id))
    db.add(KOSyncProgress(user_id=acc.id, document=document, progress=progress,
                          percentage=percentage, device=device,
                          timestamp=int(time.time()) - age_seconds))
    db.flush()
    return acc


def _seed_plugin_position(db, user, book, percentage, age_seconds=0, device="kindle"):
    db.add(TomeSyncPosition(
        user_id=user.id, book_id=book.id, percentage=percentage,
        progress="/body/DocFragment[5]/body/p[1]", device=device,
        updated_at=datetime.utcnow() - timedelta(seconds=age_seconds)))
    db.flush()


# ── flag off: behaviour unchanged ─────────────────────────────────────────────

def test_flag_off_ignores_newer_kosync_push(ts_client, db, make_book):
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_plugin_position(db, user, book, 0.839, age_seconds=7200)
    _seed_kosync_push(db, user, book, 0.859)  # newer, but the flag is off
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["percentage"] == 0.839
    assert r.json()["device"] == "kindle"


def test_flag_off_404_when_only_kosync_data_exists(ts_client, db, make_book):
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_kosync_push(db, user, book, 0.859)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 404


# ── flag on: newest known position wins ───────────────────────────────────────

def test_newer_kosync_push_served_to_plugin(ts_client, db, make_book, monkeypatch):
    # The #175 repro: a KOSync client at 85.9%, plugin device last pushed
    # 83.9% — the plugin's pull must now see 85.9%.
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_plugin_position(db, user, book, 0.839, age_seconds=7200)
    _seed_kosync_push(db, user, book, 0.859)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["percentage"] == 0.859
    assert body["device"] == "crosspoint"
    assert body["progress"] == "/body/DocFragment[8]/body/p[3]"  # locator as-is


def test_older_kosync_push_loses_to_plugin_position(ts_client, db, make_book, monkeypatch):
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_kosync_push(db, user, book, 0.20, age_seconds=7200)
    _seed_plugin_position(db, user, book, 0.55)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.json()["percentage"] == 0.55
    assert r.json()["device"] == "kindle"


def test_kosync_only_satisfies_pull(ts_client, db, make_book, monkeypatch):
    # Book only ever read on the third-party client: the pull must serve it
    # instead of 404ing.
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_kosync_push(db, user, book, 0.42)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["percentage"] == 0.42


def test_newest_of_multiple_kosync_pushes_wins(ts_client, db, make_book, monkeypatch):
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_kosync_push(db, user, book, 0.30, age_seconds=7200, device="readest",
                      document="cd" * 16)
    _seed_kosync_push(db, user, book, 0.60, device="crosspoint")
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.json()["percentage"] == 0.60
    assert r.json()["device"] == "crosspoint"


def test_no_kosync_data_404_unchanged(ts_client, db, make_book, monkeypatch):
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, _user, headers = ts_client
    book = make_book(title="Bridge Book")

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 404


# ── guarantees ────────────────────────────────────────────────────────────────

def test_bridge_read_never_writes_tomesync_position(ts_client, db, make_book, monkeypatch):
    # The #156 write-side rule holds: serving a bridged position must not
    # materialise it as TomeSyncPosition.
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    _seed_kosync_push(db, user, book, 0.42)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.status_code == 200
    assert db.query(TomeSyncPosition).count() == 0


def test_bridge_does_not_leak_other_users_pushes(ts_client, db, make_book, monkeypatch):
    # Same book, but the KOSync push belongs to a different Tome user.
    monkeypatch.setattr(settings, "kosync_position_bridge", True)
    c, user, headers = ts_client
    book = make_book(title="Bridge Book")
    other = User(username="otheruser", email="other@example.com",
                 hashed_password=hash_password("pass"), is_active=True,
                 role="member", must_change_password=False)
    db.add(other)
    db.flush()
    _seed_kosync_push(db, other, book, 0.90)
    _seed_plugin_position(db, user, book, 0.10)
    db.commit()

    r = c.get(f"/api/tome-sync/position/{book.id}", headers=headers)
    assert r.json()["percentage"] == 0.10
