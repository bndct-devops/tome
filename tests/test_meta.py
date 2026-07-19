"""Tests for /api/meta — What's-New changelog parsing and the update check."""
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

import backend.api.meta as meta
from backend.core.config import settings
from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission


SAMPLE = """# Changelog

## [Unreleased]

### Added
- **Unreleased thing.** Not shipped yet.

## [1.8.0]

### Added
- **Sync closed books.** Walks the device for books the plugin never synced,
  adopting status and rating from the sidecar.
- Plain bullet without a bold lead.

### Fixed
- **Chapter maps extract from EPUB2 books.** NCX fallback.

## [1.7.0]

### Added
- **Older thing.** Should never appear for 1.8.0.
"""


def test_parse_matches_version_section():
    entries = meta.parse_changelog_section(SAMPLE, "1.8.0")
    assert len(entries) == 3
    assert entries[0]["kind"] == "Added"
    assert entries[0]["title"] == "Sync closed books."
    assert "adopting status and rating" in entries[0]["body"]  # continuation folded
    assert entries[1]["title"] == ""
    assert entries[1]["body"] == "Plain bullet without a bold lead."
    assert entries[2]["kind"] == "Fixed"
    assert all("Older thing" not in (e["title"] + e["body"]) for e in entries)


def test_parse_falls_back_to_unreleased():
    entries = meta.parse_changelog_section(SAMPLE, "9.9.9")
    assert len(entries) == 1
    assert entries[0]["title"] == "Unreleased thing."


def test_whats_new_endpoint_shape(client: TestClient):
    r = client.get("/api/meta/whats-new")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert isinstance(data["entries"], list)


def _reset_cache():
    meta._cache.update(at=0.0, result=None, ttl=0.0)


def test_update_check_available(client: TestClient, monkeypatch):
    _reset_cache()
    monkeypatch.setattr(meta, "_fetch_latest_release", lambda: "99.0.0")
    r = client.get("/api/meta/update-check")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["latest"] == "99.0.0"
    assert data["update_available"] is True
    assert data["url"].startswith("https://github.com/")


def test_update_check_current_is_latest(client: TestClient, monkeypatch):
    _reset_cache()
    from backend import __version__
    monkeypatch.setattr(meta, "_fetch_latest_release", lambda: __version__)
    data = client.get("/api/meta/update-check").json()
    assert data["update_available"] is False


def test_update_check_cached(client: TestClient, monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return "99.0.0"

    monkeypatch.setattr(meta, "_fetch_latest_release", fetch)
    client.get("/api/meta/update-check")
    client.get("/api/meta/update-check")
    assert calls["n"] == 1


def test_update_check_network_failure_is_graceful(client: TestClient, monkeypatch):
    _reset_cache()

    def boom():
        raise OSError("offline")

    monkeypatch.setattr(meta, "_fetch_latest_release", boom)
    data = client.get("/api/meta/update-check").json()
    assert data["update_available"] is False
    assert data["latest"] is None


def test_update_check_disabled_by_env(client: TestClient, monkeypatch):
    _reset_cache()
    monkeypatch.setattr(settings, "update_check", False)
    data = client.get("/api/meta/update-check").json()
    assert data["enabled"] is False
    assert data["update_available"] is False


def test_update_check_admin_only(client: TestClient, db: Session, monkeypatch):
    _reset_cache()
    member = User(username="meta_member", email="meta_member@example.com",
                  hashed_password=hash_password("pass1234"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    token = create_access_token(subject=member.id)
    r = client.get("/api/meta/update-check", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
