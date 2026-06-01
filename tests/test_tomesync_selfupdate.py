"""Tests for the TomeSync self-update surface (shim/impl split + endpoints).

See docs/tomesync-self-update-plan.md.
"""
import io
import zipfile

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission
from backend.api.tome_sync import (
    TOMESYNC_PLUGIN_BUILD,
    TOMESYNC_PLUGIN_SEMVER,
    _main_shim_lua,
    _main_impl_lua,
)


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
    db.add(UserPermission(user_id=user.id, can_upload=True, can_download=True))
    db.flush()
    return user, create_access_token(subject=user.id)


@pytest.fixture()
def app_client(db: Session):
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, db
    app.dependency_overrides.clear()


# ── /plugin/version: back-compat + new fields ────────────────────────────────

def test_plugin_version_returns_build_and_semver(app_client):
    c, _ = app_client
    r = c.get("/api/plugin/version")
    assert r.status_code == 200
    body = r.json()
    # back-compat: version stays a build-int-as-string
    assert body["version"] == str(TOMESYNC_PLUGIN_BUILD)
    assert body["build"] == TOMESYNC_PLUGIN_BUILD
    assert isinstance(body["build"], int)
    assert body["semver"] == TOMESYNC_PLUGIN_SEMVER


# ── Plugin zip: three files, config only in impl ─────────────────────────────

def test_plugin_zip_has_shim_and_impl(app_client):
    c, db = app_client
    _, token = _make_user(db, "zipuser", "member")
    r = c.get("/api/plugin/koreader", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert "tomesync.koplugin/_meta.lua" in names
    assert "tomesync.koplugin/main.lua" in names
    assert "tomesync.koplugin/main_impl.lua" in names

    shim = zf.read("tomesync.koplugin/main.lua").decode()
    impl = zf.read("tomesync.koplugin/main_impl.lua").decode()
    # Config is baked into the impl, never the frozen shim.
    assert "local API_KEY" in impl and "local SERVER_URL" in impl
    assert "API_KEY" not in shim or "local API_KEY" not in shim
    assert "main_impl.lua" in shim  # shim loads the impl


# ── /plugin/main-impl.lua: authenticated, config-baked, self-validating ──────

def test_main_impl_requires_auth(app_client):
    c, _ = app_client
    # No Authorization header → dependency rejects with 422/401, never 200.
    r = c.get("/api/plugin/main-impl.lua")
    assert r.status_code in (401, 422)


def test_main_impl_served_with_baked_config(app_client):
    c, db = app_client
    _, token = _make_user(db, "impluser", "member")
    # Mint an API key for this user.
    rk = c.post(
        "/api/plugin/api-keys",
        json={"label": "KO"},
        headers={"Authorization": f"Bearer {token}"},
    )
    key = rk.json()["key"]

    r = c.get("/api/plugin/main-impl.lua", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # The caller's key is baked in so config survives the update.
    assert key in body
    assert "impluser" in body
    # Passes the same sentinels the plugin checks before swapping.
    assert len(body) > 15000
    assert "function TomeSync:init" in body
    assert "return TomeSync" in body


# ── Generated Lua invariants ─────────────────────────────────────────────────

def test_shim_is_config_free_and_loads_impl():
    shim = _main_shim_lua()
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    # Shim carries no secrets and no per-user config.
    assert "tome_secret_key" not in shim
    assert "https://tome.example" not in shim
    assert "alice" not in shim
    # Shim runs the rollback machine and loads the impl.
    assert "main_impl.lua" in shim
    assert "dofile" in shim
    assert "tomesync_update" in shim
    # Impl bakes config and carries the build/semver constants.
    assert "tome_secret_key" in impl
    assert f"local BUILD           = {TOMESYNC_PLUGIN_BUILD}" in impl
    assert f'local SEMVER          = "{TOMESYNC_PLUGIN_SEMVER}"' in impl
