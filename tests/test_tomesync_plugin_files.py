"""The plugin as files (build 54): backend/tomesync.koplugin, config.lua.

The Lua used to be Python strings with the connection baked in. Now the impl
is one file for everyone and the connection is config.lua, rendered by
_config_lua (zip download, GET /plugin/config.lua) and by
tools/bake-tomesync.py (the iOS app's build).
"""
import io
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.api.tome_sync import _config_lua, _main_impl_lua, _main_shim_lua, _meta_lua
from backend.core.database import get_db
from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "backend" / "tomesync.koplugin"
LUAJIT = shutil.which("luajit")
needs_luajit = pytest.mark.skipif(LUAJIT is None, reason="luajit not installed")


def _run_lua(tmp_path, script: str) -> str:
    path = tmp_path / "spec.lua"
    path.write_text(script)
    out = subprocess.run([LUAJIT, str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout


# ── Python surface reads the files ───────────────────────────────────────────

def test_python_returns_the_files():
    assert _meta_lua() == (PLUGIN / "_meta.lua").read_text()
    assert _main_shim_lua() == (PLUGIN / "main.lua").read_text()
    assert _main_impl_lua() == (PLUGIN / "main_impl.lua").read_text()
    # Arguments are accepted for old callers and change nothing.
    assert _main_impl_lua("https://x", "tk_secret", "bob", True, "AB") == _main_impl_lua()


def test_impl_has_no_python_escaping_and_no_baked_values():
    impl = _main_impl_lua()
    # No f-string escaping left: it doubled every brace, so an empty table
    # read {{}}. The only {{ left are ButtonDialog rows (a list of rows of
    # buttons), which KOReader writes that way.
    assert "{{}}" not in impl
    for line in impl.splitlines():
        if "{{" in line:
            assert re.fullmatch(r"\s*buttons\s*=\s*\{\{", line), line
        if "}}" in line:
            assert re.fullmatch(r"\s*\}\},", line), line
    # Nor Python %-format slots (%( itself is Lua's escaped paren in patterns).
    assert not re.search(r"%\(\w+\)[sdr]", impl)
    assert "tk_" not in impl
    assert 'local DEFAULT_API_KEY    = PLUGIN_CONFIG.API_KEY or ""' in impl
    assert 'local DEFAULT_SERVER_URL = PLUGIN_CONFIG.SERVER_URL or ""' in impl


# ── _config_lua: Lua string escaping ─────────────────────────────────────────

AWKWARD = {
    "server_url": 'http://h/"quoted"\\path',
    "api_key": "tk_a\nb\rc\td\x00e\x7f",
    "username": 'O\'Brien "the" \\ reader ]] --[[',
}


def test_config_lua_escapes_quotes_backslashes_newlines():
    cfg = _config_lua(**AWKWARD)
    lines = cfg.splitlines()
    assert len(lines) == 6  # a comment and five assignments, each on one line
    assert lines[1] == 'SERVER_URL = "http://h/\\"quoted\\"\\\\path"'
    assert lines[2] == 'API_KEY = "tk_a\\nb\\rc\\009d\\000e\\127"'
    assert lines[4] == "AUTO_DELIVER = false"


@needs_luajit
def test_config_lua_round_trips_under_luajit(tmp_path):
    cfg = _config_lua(**AWKWARD, auto_deliver=True, server_fingerprint="ab:CD")
    (tmp_path / "config.lua").write_text(cfg)
    expect = {"SERVER_URL": AWKWARD["server_url"], "API_KEY": AWKWARD["api_key"],
              "USERNAME": AWKWARD["username"], "SERVER_FINGERPRINT": "ab:CD"}
    checks = "\n".join(
        f'assert(env.{k} == {_lua_bytes(v)}, "{k}")' for k, v in expect.items()
    )
    _run_lua(tmp_path, f'''
local chunk = assert(loadfile("{tmp_path}/config.lua"))
local env = {{}}
setfenv(chunk, env)
chunk()
{checks}
assert(env.AUTO_DELIVER == true, "auto")
print("ok")
''')


def _lua_bytes(v: str) -> str:
    """A Lua literal built byte by byte (independent of _config_lua)."""
    return '"' + "".join("\\%d" % b for b in v.encode()) + '"'


# ── Zip download: four files ─────────────────────────────────────────────────

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


def test_zip_has_four_files(app_client):
    c, db = app_client
    user = User(username="four", email="four@example.com", hashed_password=hash_password("p"),
                is_active=True, is_admin=False, role="member", must_change_password=False)
    db.add(user)
    db.flush()
    db.add(UserPermission(user_id=user.id, can_upload=True, can_download=True))
    db.flush()
    r = c.get("/api/plugin/koreader",
              headers={"Authorization": f"Bearer {create_access_token(subject=user.id)}"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == sorted(f"tomesync.koplugin/{n}" for n in
                                           ("_meta.lua", "main.lua", "main_impl.lua", "config.lua"))
    assert zf.read("tomesync.koplugin/main_impl.lua").decode() == _main_impl_lua()
    assert zf.read("tomesync.koplugin/main.lua").decode() == _main_shim_lua()
    config = zf.read("tomesync.koplugin/config.lua").decode()
    key = config.split('API_KEY = "')[1].split('"')[0]
    assert config == _config_lua("http://testserver", key, "four")


# ── The impl's config reader under luajit ────────────────────────────────────

def _config_block() -> str:
    impl = _main_impl_lua()
    return impl[impl.index("-- TOMESYNC_CONFIG_BEGIN"):impl.index("-- TOMESYNC_CONFIG_END")]


@needs_luajit
def test_config_reader_with_and_without_config(tmp_path):
    with_cfg = tmp_path / "with"
    with_cfg.mkdir()
    (with_cfg / "config.lua").write_text(
        _config_lua('http://h:1/"x"', "tk_with", "ann\nb", auto_deliver=True, server_fingerprint="AB:CD"))
    without = tmp_path / "without"
    without.mkdir()
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "config.lua").write_text("SERVER_URL = ")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "config.lua").write_text('API_KEY = 7\nUSERNAME = os and os.exit(3) or "u"\n')
    _run_lua(tmp_path, _config_block() + f'''
local c, from = PluginConfig.load("{with_cfg}")
assert(from == "config", from)
assert(c.SERVER_URL == 'http://h:1/"x"' and c.API_KEY == "tk_with", "strings")
assert(c.USERNAME == "ann\\nb" and c.AUTO_DELIVER == true and c.SERVER_FINGERPRINT == "AB:CD", "rest")

c, from = PluginConfig.load("{without}")
assert(from == "none" and next(c) == nil, "absent: empty, nothing written")
assert(io.open("{without}/config.lua") == nil, "absent stays absent")

c, from = PluginConfig.load("{invalid}")
assert(from == "invalid" and next(c) == nil, "a broken config.lua reads as empty")

c, from = PluginConfig.load("{hostile}")
assert(from == "config" and c.API_KEY == nil, "wrong type dropped")
assert(c.USERNAME == "u", "no os in scope")
print("ok")
''')


# A build-53 impl as Tome rendered it: the five lines that matter, unescaped.
BAKED_53 = '''--[[ old impl ]]
local logger = require("logger")
-- Defaults baked in at download time; overridable in Settings > Server.
local DEFAULT_SERVER_URL = "http://tome.lan:8080"
local DEFAULT_API_KEY    = "tk_oldbaked"
local DEFAULT_USERNAME   = "benedict"
-- Inbox auto-pickup default (Settings > "Download inbox automatically" wins).
local DEFAULT_AUTO_DELIVER = true
local SERVER_FINGERPRINT = ""
return {}
'''

# Before build 53 there was no auto-deliver and no fingerprint line.
BAKED_OLD = '''local DEFAULT_SERVER_URL = "https://tome.example.org"
local DEFAULT_API_KEY    = "tk_older"
local DEFAULT_USERNAME   = "alice"
'''


@needs_luajit
@pytest.mark.parametrize("baked,want", [
    (BAKED_53, ("http://tome.lan:8080", "tk_oldbaked", "benedict", "true", "")),
    (BAKED_OLD, ("https://tome.example.org", "tk_older", "alice", "false", "")),
])
def test_migration_reads_baked_backup_and_writes_config(tmp_path, baked, want):
    d = tmp_path / "plugin"
    d.mkdir()
    (d / "main_impl.lua.bak").write_text(baked)
    url, key, user, auto, fp = want
    _run_lua(tmp_path, _config_block() + f'''
local c, from = PluginConfig.load("{d}")
assert(from == "baked", from)
assert(c.SERVER_URL == "{url}" and c.API_KEY == "{key}" and c.USERNAME == "{user}", "values")
assert((c.AUTO_DELIVER == true) == {auto}, "auto")
c, from = PluginConfig.load("{d}")
assert(from == "config", "written once, read from config.lua after: " .. from)
assert(c.API_KEY == "{key}" and (c.AUTO_DELIVER == true) == {auto}, "same values")
print("ok")
''')
    assert (d / "config.lua").read_text() == _config_lua(url, key, user, auto == "true", fp)


@needs_luajit
def test_new_impl_as_backup_is_not_mistaken_for_baked(tmp_path):
    d = tmp_path / "plugin"
    d.mkdir()
    (d / "main_impl.lua.bak").write_text(_main_impl_lua())
    _run_lua(tmp_path, _config_block() + f'''
local c, from = PluginConfig.load("{d}")
assert(from == "none", from)
print("ok")
''')


@needs_luajit
def test_render_matches_python(tmp_path):
    out = _run_lua(tmp_path, _config_block() + r'''
io.write(PluginConfig.render({ SERVER_URL = "http://a", API_KEY = "tk_x", USERNAME = "u",
                               AUTO_DELIVER = false, SERVER_FINGERPRINT = "" }))
''')
    assert out == _config_lua("http://a", "tk_x", "u")


# ── tools/bake-tomesync.py ───────────────────────────────────────────────────

def test_bake_tool_writes_the_four_files(tmp_path):
    out = tmp_path / "tomesync.koplugin"
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools" / "bake-tomesync.py"), str(out),
         "--server-url", 'https://192.168.1.5:8899', "--api-key", 'tk_"k"\\', "--username", "me",
         "--auto-deliver", "--fingerprint", "AB:CD:zz"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["_meta.lua", "config.lua", "main.lua", "main_impl.lua"]
    for name in ("_meta.lua", "main.lua", "main_impl.lua"):
        assert (out / name).read_bytes() == (PLUGIN / name).read_bytes()
    assert (out / "config.lua").read_text() == _config_lua(
        "https://192.168.1.5:8899", 'tk_"k"\\', "me", True, "AB:CD:zz")


@needs_luajit
def test_link_hosts_kept_https_only_and_rendered_like_python(tmp_path):
    d = tmp_path / "plugin"
    d.mkdir()
    hosts = ("https://192.168.1.5:8899", "https://172.20.10.1:8899")
    (d / "config.lua").write_text(_config_lua("https://192.168.1.5:8899", "tk_x", "u",
                                              True, "AB:CD", link_hosts=hosts))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "config.lua").write_text(
        'LINK_HOSTS = { "http://plain:1", 7, "https://ok:2", "https://ok:2", "https://a b:3", "https://x/path" }\n')
    out = _run_lua(tmp_path, _config_block() + f'''
local c = PluginConfig.load("{d}")
assert(#c.LINK_HOSTS == 2 and c.LINK_HOSTS[1] == "https://192.168.1.5:8899"
       and c.LINK_HOSTS[2] == "https://172.20.10.1:8899", "hosts")
local b = PluginConfig.load("{bad}")
assert(#b.LINK_HOSTS == 1 and b.LINK_HOSTS[1] == "https://ok:2", "only https host:port, once")
local none = PluginConfig.parse('LINK_HOSTS = {{ "http://x:1" }}')
assert(none.LINK_HOSTS == nil, "nothing usable: no list")
io.write(PluginConfig.render(c))
''')
    assert out == (d / "config.lua").read_text()
    # Without hosts the file is the five lines Tome has always written.
    assert "LINK_HOSTS" not in _config_lua("https://a", "tk", "u")


def test_bake_tool_writes_link_hosts(tmp_path):
    out = tmp_path / "tomesync.koplugin"
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools" / "bake-tomesync.py"), str(out),
         "--server-url", "https://10.0.0.2:8899", "--api-key", "k", "--username", "me",
         "--fingerprint", "AB", "--link-host", "https://10.0.0.2:8899",
         "--link-host", "https://172.20.10.1:8899"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (out / "config.lua").read_text()
    assert text == _config_lua("https://10.0.0.2:8899", "k", "me", False, "AB",
                               link_hosts=("https://10.0.0.2:8899", "https://172.20.10.1:8899"))
    assert text.endswith('LINK_HOSTS = { "https://10.0.0.2:8899", "https://172.20.10.1:8899" }\n')


def test_config_example_parses_like_a_config():
    text = (PLUGIN / "config.lua.example").read_text()
    for name in ("SERVER_URL", "API_KEY", "USERNAME", "AUTO_DELIVER", "SERVER_FINGERPRINT"):
        assert f"\n{name} = " in text
