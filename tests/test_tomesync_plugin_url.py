"""Regression tests for the URL baked into the KOReader plugin's SERVER_URL.

Background: on HTTPS deployments behind a TLS-terminating reverse proxy, the app
server sees ``http``; if the proxy then redirects HTTP→HTTPS, KOReader can't
follow the 307 on POST/PUT and every sync fails. The bake must honour
``X-Forwarded-Proto`` (and an explicit ``TOME_PUBLIC_URL``) so it produces
``https`` for those deployments — while leaving plain HTTP / LAN / localhost
deployments baking exactly what they did before.
"""
import io
import re
import zipfile

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

import backend.api.tome_sync as tome_sync
from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission
from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD, TOMESYNC_PLUGIN_SEMVER


def _make_user(db: Session, username: str) -> tuple[User, str]:
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


def _baked_url(impl_text: str) -> str:
    m = re.search(r'local SERVER_URL\s*=\s*"([^"]+)"', impl_text)
    assert m, "SERVER_URL not found in baked impl"
    return m.group(1)


def _impl_via_self_update(c: TestClient, token: str, **kwargs) -> str:
    """Mint a key, hit /plugin/main-impl.lua, return the baked impl text."""
    rk = c.post("/api/plugin/api-keys", json={"label": "KO"},
                headers={"Authorization": f"Bearer {token}"})
    key = rk.json()["key"]
    r = c.get("/api/plugin/main-impl.lua",
              headers={"Authorization": f"Bearer {key}", **kwargs.get("headers", {})},
              params=kwargs.get("params"))
    assert r.status_code == 200, r.text
    return r.text


# ── The fix ──────────────────────────────────────────────────────────────────

def test_plain_request_bakes_http_unchanged(app_client):
    """No forwarded header, no TOME_PUBLIC_URL → scheme is left as-is (http).

    Proves LAN / localhost / plain-HTTP deployments are unaffected by the fix.
    """
    c, db = app_client
    _, token = _make_user(db, "plain")
    assert _baked_url(_impl_via_self_update(c, token)) == "http://testserver"


def test_forwarded_proto_https_bakes_https(app_client):
    """A proxy that terminates TLS sends X-Forwarded-Proto: https → bake https."""
    c, db = app_client
    _, token = _make_user(db, "fwd")
    impl = _impl_via_self_update(c, token, headers={"X-Forwarded-Proto": "https"})
    assert _baked_url(impl) == "https://testserver"


def test_forwarded_proto_chain_takes_first_hop(app_client):
    """X-Forwarded-Proto may be a comma chain; the client-facing scheme is first."""
    c, db = app_client
    _, token = _make_user(db, "chain")
    impl = _impl_via_self_update(c, token, headers={"X-Forwarded-Proto": "https, http"})
    assert _baked_url(impl) == "https://testserver"


def test_public_url_overrides_everything(app_client, monkeypatch):
    """TOME_PUBLIC_URL wins even against a (wrong) forwarded header."""
    c, db = app_client
    _, token = _make_user(db, "pub")
    monkeypatch.setattr(tome_sync.settings, "public_url", "https://tome.example.org")
    impl = _impl_via_self_update(c, token, headers={"X-Forwarded-Proto": "http"})
    assert _baked_url(impl) == "https://tome.example.org"


def test_public_url_trailing_slash_trimmed(app_client, monkeypatch):
    c, db = app_client
    _, token = _make_user(db, "pubslash")
    monkeypatch.setattr(tome_sync.settings, "public_url", "https://tome.example.org/")
    assert _baked_url(_impl_via_self_update(c, token)) == "https://tome.example.org"


def test_explicit_server_url_param_wins(app_client):
    c, db = app_client
    _, token = _make_user(db, "explicit")
    impl = _impl_via_self_update(
        c, token,
        params={"server_url": "https://pinned.example"},
        headers={"X-Forwarded-Proto": "http"},
    )
    assert _baked_url(impl) == "https://pinned.example"


# ── Same logic on the initial ZIP download ───────────────────────────────────

def test_zip_download_bakes_https_with_forwarded_proto(app_client):
    c, db = app_client
    _, token = _make_user(db, "zipfwd")
    r = c.get("/api/plugin/koreader",
              headers={"Authorization": f"Bearer {token}", "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    impl = zf.read("tomesync.koplugin/main_impl.lua").decode()
    assert _baked_url(impl) == "https://testserver"


# ── Release guard: the build MUST be bumped or devices won't re-bake ──────────

def test_build_bumped_for_rebake():
    # Must exceed every build already live (v1.2.0 shipped 10; main reached 12),
    # so all existing installs re-download and re-bake the corrected URL.
    # 1.5.0 / build 20 adds bidirectional book-rating sync (KOReader's native
    # star rating + review <-> Tome) on top of 19's download path templates.
    assert TOMESYNC_PLUGIN_BUILD >= 20
    assert TOMESYNC_PLUGIN_SEMVER == "1.5.0"
