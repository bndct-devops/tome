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
    # 1.5.0 / build 20 added bidirectional book-rating sync (KOReader's native
    # star rating + review <-> Tome) on top of 19's download path templates.
    # 1.5.1 / build 21 queues ratings set offline so a finished book you never
    # reopen still syncs its rating (the per-book open/close push alone missed it).
    # 1.6.0 / build 22 imports KOReader's statistics.sqlite3 (per-page reading
    # history) so stats backfill reading from before TomeSync (time & pages only).
    # 1.6.1 / build 23 makes the sync back-off time-based (+ clears on
    # NetworkConnected) so it self-heals instead of latching offline after sleep.
    # 1.6.2 / build 24 files each download under its own book type (issue #88):
    # the No Series bucket mixes types, so a single batch type misfiled standalone
    # books (e.g. RoyalRoad titles landing in light_novel).
    # 1.7.0 / build 25 adopts web-created highlights: annotations made in Tome's
    # web reader arrive under a provisional "web:" anchor; the plugin locates the
    # text natively, creates a first-class KOReader highlight, and the sync push
    # (adopted_from) retires the provisional server-side.
    # 1.7.1 / build 26 makes the reading-history backfill memory-bounded:
    # keyset-windowed reads over (start_time, rowid) instead of loading every
    # page-stat row since the watermark at once, and each upload chunk carries
    # only the books it references. Verified against a 34k-row device DB.
    # 1.7.2 / build 27 adds deterministic book identity: the plugin sends the
    # file's KOReader partial-MD5 with resolve calls; the server matches it
    # against ko_hashes (recorded at scan/serve time) before any filename
    # heuristics — renamed/moved device files resolve exactly.
    # 1.7.3 / build 28 verifies foreign highlights before painting them: the
    # anchor must reproduce its highlighted text on this copy; mismatches are
    # repaired by text search (rendered locally, server identity kept via
    # repair_map — no cross-device anchor ping-pong); unlocatable text is
    # skipped rather than painted on the wrong words.
    # 1.7.4 / build 29 is the UX batch: bounded socketutil timeouts on every
    # request (no more global http.TIMEOUT mutation; a dead server stalls the
    # UI seconds, not a minute), failed fetches/downloads offer Retry,
    # downloads show live byte/percent progress, a single fresh download
    # offers "Open now?", volume rows are marked "· on device", and the
    # position heartbeat is idle-debounced off the page-turn path. (A Trapper
    # subprocess variant for fetches was built and dropped: forked sockets
    # from plugin context proved unverifiable on the emulator.)
    # Build numbering note: TWO build 26s shipped from parallel branches on
    # 2026-07-03 (main: the paging-annotation guard; the foundations stack:
    # the memory-bounded backfill). The merge resolves to build 30 so every
    # device re-fetches regardless of which 26 it took; 27-29 are the
    # foundations-stack builds (identity+clustering, verify/repair, UX batch),
    # and the paging guard's semantics are folded into _applyForeign.
    # 1.8.0 / build 31 introduces half-star ratings server-side: the plugin's
    # rating_baseline splits into {remote, device} so a Tome half-star rounded
    # onto the whole-star sidecar is never pushed back as a "local edit".
    # 1.9.0 / build 35 adds sync on suspend (issue #128): opt-in catch-up of
    # pending sessions/ratings + reading-history backfill at lid close, with an
    # aggressive variant that turns WiFi on first; onNetworkConnected also runs
    # the history backfill (was launch-only) and flushes without an open book.
    # 1.10.0 / build 36 adds the Shelves browse axis: the device lists the
    # user's shelves and drills into their resolved book lists
    # (GET /tome-sync/{shelves,shelf-books}); mixed lists download per-book.
    assert TOMESYNC_PLUGIN_BUILD >= 36
    assert TOMESYNC_PLUGIN_SEMVER == "1.10.0"
