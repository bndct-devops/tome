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


def test_impl_auto_connect_is_opt_in_and_wraps_interactive_paths():
    """#38: the WiFi reconnect path is gated on the persisted opt-in setting,
    and only interactive entry points route through it — background tracking
    (page heartbeat, suspend/resume) must never wake the radio."""
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    # runWhenConnected is called in exactly one place: the gated helper.
    assert impl.count("NetworkMgr:runWhenConnected") == 1
    assert 'G_reader_settings:isTrue("tomesync_auto_connect")' in impl
    # Interactive entry points route through the gate.
    assert "whenConnected(function() self:_browseSeriesMenuImpl() end)" in impl
    assert "whenConnected(function() self:_inboxMenuImpl() end)" in impl
    assert (
        "whenConnected(function() self:_downloadCurrentBookSeriesImpl(rest_only) end)"
        in impl
    )


def test_impl_menu_has_settings_submenu_and_renamed_tracking_toggle():
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    # The ambiguous "Enabled" label is gone in favour of an explicit one.
    assert "Enabled (tap to disable)" not in impl
    assert "Tracking: on (tap to pause)" in impl
    assert "Tracking: paused (tap to resume)" in impl
    # Persistent settings live in a nested submenu.
    assert "Auto-connect WiFi when needed" in impl
    assert "sub_item_table = settings_items" in impl


def _luajit_or_skip():
    import shutil

    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    return luajit


def test_download_template_renderer_behaviour(tmp_path):
    """Extracts the marked renderDownloadPath block from the generated impl
    and exercises it standalone — token rendering, padding, case modifiers,
    empty-token cleanup, and path-safety guarantees."""
    import subprocess

    luajit = _luajit_or_skip()
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    start = impl.index("-- TOMESYNC_TEMPLATE_BEGIN")
    end = impl.index("-- TOMESYNC_TEMPLATE_END")
    block = impl[start:end]

    script = """
local util = { getSafeFilename = function(s)
    return (s:gsub('[\\\\:%*%?"<>|]', '_'))
end }
""" + block + """
local function eq(got, want, label)
    if got ~= want then
        io.stderr:write(string.format("FAIL %s: got %q want %q\\n",
            label, tostring(got), tostring(want)))
        os.exit(1)
    end
end
local series = { book_type = "manga", series = "Berserk", volume = 3,
                 title = "The Egg of the King", author = "Kentaro Miura" }
local standalone = { book_type = "novels", series = "", volume = nil,
                     title = "Standalone", author = "Some Author" }

-- flat preset: collision-safe for series, clean for standalones
local FLAT = "{series} - {volume:00} - {title}"
eq(renderDownloadPath(FLAT, series), "Berserk - 03 - The Egg of the King", "flat series")
eq(renderDownloadPath(FLAT, standalone), "Standalone", "flat standalone")

-- folders, zero-pad, fractional volumes
eq(renderDownloadPath("{book_type}/{series}/{volume:00} - {title}", series),
   "manga/Berserk/03 - The Egg of the King", "nested")
eq(renderDownloadPath("{book_type}/{series}/{volume:00} - {title}", standalone),
   "novels/Standalone", "nested standalone drops empty segment")
eq(renderDownloadPath("{volume}", { volume = 1.5, title = "t" }), "1.5", "fractional volume")

-- case modifiers
eq(renderDownloadPath("{Lower(series)}/{title}", series),
   "berserk/The Egg of the King", "Lower")
eq(renderDownloadPath("{Upper(book_type)}/{title}", series),
   "MANGA/The Egg of the King", "Upper")

-- safety: unknown token rejects, traversal and slashes cannot escape
eq(renderDownloadPath("{nope}/{title}", series), nil, "unknown token")
eq(renderDownloadPath("../{title}", series), "The Egg of the King", "dotdot dropped")
eq(renderDownloadPath("{series}/../../{title}", series),
   "Berserk/The Egg of the King", "dotdot segments dropped")
eq(renderDownloadPath("{title}", { title = "A/B" }), "A-B", "slash in value")
eq(renderDownloadPath("{series}", standalone), nil, "renders empty -> nil")
print("ALL OK")
"""
    path = tmp_path / "template_spec.lua"
    path.write_text(script)
    proc = subprocess.run([luajit, str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ALL OK" in proc.stdout


def test_impl_download_template_invariants():
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    # Setting key, menu entry, presets and dialog are all present.
    assert "tomesync_download_template" in impl
    assert "Download location & naming" in impl
    assert "Flat in home folder" in impl
    assert 'local FLAT_TEMPLATE = "{series} - {volume:00} - {title}"' in impl
    # Unset template must keep the built-in layout code path.
    assert "ensureDefaultDirs" in impl
    assert 'display_title = "Vol. " .. tostring(vol)' in impl


def test_impl_compiles_under_luajit(tmp_path):
    """Guards the f-string brace escaping: a stray single brace renders broken
    Lua that validateImpl would reject on-device."""
    import shutil
    import subprocess

    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    impl = _main_impl_lua("https://tome.example", "tome_secret_key", "alice")
    path = tmp_path / "main_impl.lua"
    path.write_text(impl)
    proc = subprocess.run(
        [luajit, "-e", f"assert(loadfile({str(path)!r}))"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
