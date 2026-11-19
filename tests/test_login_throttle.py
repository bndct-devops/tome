"""Backoff on password guessing (web login, OPDS Basic, KOSync) and stronger
OPDS PINs at rest."""
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core import login_throttle
from backend.core.login_throttle import LoginThrottle
from backend.models.kosync import KOSyncUser
from backend.models.opds_pin import OpdsPin

ADMIN = {"username": "testadmin", "password": "adminpass123"}
WRONG = {"username": "testadmin", "password": "not-the-password"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(login_throttle.throttle, "clock", c)
    return c


# ── the backoff itself ─────────────────────────────────────────────────────

def test_backoff_starts_after_five_failures_and_doubles_to_a_cap():
    c = Clock()
    t = LoginThrottle(clock=c)
    key = t.key("Reader", "10.0.0.1")
    for _ in range(4):
        t.failure(key)
    assert t.retry_after(key) == 0
    t.failure(key)  # 5th
    assert t.retry_after(key) == 1
    t.failure(key)
    assert t.retry_after(key) == 2
    for _ in range(10):
        t.failure(key)
    assert t.retry_after(key) == 60
    c.now += 60
    assert t.retry_after(key) == 0


def test_key_is_username_and_ip_and_ignores_case():
    t = LoginThrottle(clock=Clock())
    assert t.key("Reader", "1.2.3.4") == t.key("reader ", "1.2.3.4")
    for _ in range(5):
        t.failure(t.key("alice", "1.2.3.4"))
    assert t.retry_after(t.key("alice", "1.2.3.4")) > 0
    # Same IP, other user: not affected (a reverse proxy gives everyone one IP).
    assert t.retry_after(t.key("bob", "1.2.3.4")) == 0
    assert t.retry_after(t.key("alice", "5.6.7.8")) == 0


def test_success_clears_and_idle_entries_expire():
    c = Clock()
    t = LoginThrottle(clock=c)
    key = t.key("alice", "ip")
    for _ in range(5):
        t.failure(key)
    t.success(key)
    assert t.retry_after(key) == 0
    for _ in range(5):
        t.failure(key)
    c.now += 15 * 60 + 1
    assert t.retry_after(key) == 0
    t.failure(key)  # counting starts over after the idle expiry
    assert t.retry_after(key) == 0


# ── web login ──────────────────────────────────────────────────────────────

def test_login_backs_off_after_five_failures(client: TestClient, clock, monkeypatch):
    for _ in range(5):
        assert client.post("/api/auth/login", json=WRONG).status_code == 401
    # Refused without checking the password: even the right one gets 429.
    calls = []
    import backend.api.auth as auth_mod
    real = auth_mod.verify_password
    monkeypatch.setattr(auth_mod, "verify_password", lambda *a: calls.append(a) or real(*a))
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "1"
    assert calls == []
    clock.now += 1
    assert client.post("/api/auth/login", json=ADMIN).status_code == 200
    # The success cleared the key.
    assert client.post("/api/auth/login", json=WRONG).status_code == 401
    assert client.post("/api/auth/login", json=ADMIN).status_code == 200


def test_login_backoff_is_per_username(client: TestClient, clock):
    for _ in range(6):
        client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert client.post("/api/auth/login", json=ADMIN).status_code == 200


def test_login_backoff_key_ignores_username_case(client: TestClient, clock):
    for name in ("TestAdmin", "testadmin", "TESTADMIN", "testAdmin", "testadmin"):
        client.post("/api/auth/login", json={"username": name, "password": "x"})
    assert client.post("/api/auth/login", json=ADMIN).status_code == 429


# ── OPDS Basic ─────────────────────────────────────────────────────────────

def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_opds_basic_backs_off(client: TestClient, clock):
    del client.headers["Authorization"]
    for _ in range(5):
        assert client.get("/opds/", headers=_basic("testadmin", "wrong")).status_code == 401
    r = client.get("/opds/", headers=_basic("testadmin", "adminpass123"))
    assert r.status_code == 429 and "Retry-After" in r.headers
    clock.now += 1
    assert client.get("/opds/", headers=_basic("testadmin", "adminpass123")).status_code == 200


# ── KOSync ─────────────────────────────────────────────────────────────────

def test_kosync_backs_off(client: TestClient, db: Session, clock):
    key = hashlib.md5(b"syncpass").hexdigest()
    db.add(KOSyncUser(username="testadmin", userkey=key))
    db.flush()
    del client.headers["Authorization"]
    bad = {"x-auth-user": "testadmin", "x-auth-key": "0" * 32}
    good = {"x-auth-user": "testadmin", "x-auth-key": key}
    for _ in range(5):
        assert client.get("/api/v1/users/auth", headers=bad).status_code == 401
    r = client.get("/api/v1/users/auth", headers=good)
    assert r.status_code == 429 and "Retry-After" in r.headers
    clock.now += 1
    assert client.get("/api/v1/users/auth", headers=good).status_code == 200


# ── OPDS PINs ──────────────────────────────────────────────────────────────

def test_new_pins_are_longer_and_keep_one_preview_character(client: TestClient):
    r = client.post("/api/opds-pins", json={"label": "Kobo"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["pin"]) == 10
    assert body["pin_preview"] == body["pin"][0] + "..."
    del client.headers["Authorization"]
    assert client.get("/opds/", headers=_basic("testadmin", body["pin"])).status_code == 200


def test_old_pins_keep_working(client: TestClient, db: Session, admin_user):
    import bcrypt

    user, _ = admin_user
    db.add(OpdsPin(user_id=user.id, label="Old", pin_preview="abc...",
                   hashed_pin=bcrypt.hashpw(b"abcdef", bcrypt.gensalt()).decode()))
    db.flush()
    del client.headers["Authorization"]
    assert client.get("/opds/", headers=_basic("testadmin", "abcdef")).status_code == 200


def test_startup_shortens_old_pin_previews():
    from sqlalchemy import create_engine
    from backend.main import _shorten_opds_pin_previews

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        _shorten_opds_pin_previews(conn)  # no table yet: no-op
        conn.execute(text("CREATE TABLE opds_pins (id INTEGER PRIMARY KEY, pin_preview VARCHAR(8))"))
        conn.execute(text("INSERT INTO opds_pins (pin_preview) VALUES ('abc...'), ('k...')"))
        conn.commit()
        _shorten_opds_pin_previews(conn)
        _shorten_opds_pin_previews(conn)  # idempotent
        rows = [r[0] for r in conn.execute(text("SELECT pin_preview FROM opds_pins ORDER BY id"))]
    assert rows == ["a...", "k..."]
