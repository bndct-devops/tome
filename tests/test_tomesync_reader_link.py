"""Reader link (plugin build 53): the bake keyword and the Lua it renders.

The routes themselves are not on Tome yet (the iOS app's listener serves them);
these tests pin the bake: auto-pickup stays off for every existing caller, the
reader link turns it on, and both renders are valid Lua.
"""
import shutil
import subprocess

import pytest

from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD, _main_impl_lua


def test_build_is_53_or_later():
    assert TOMESYNC_PLUGIN_BUILD >= 53


def test_auto_deliver_defaults_off():
    lua = _main_impl_lua("http://tome.test", "key", "user")
    assert "local DEFAULT_AUTO_DELIVER = false" in lua


def test_auto_deliver_baked_on():
    lua = _main_impl_lua("http://tome.test", "key", "user", auto_deliver=True)
    assert "local DEFAULT_AUTO_DELIVER = true" in lua


def test_reader_link_routes_present():
    lua = _main_impl_lua("http://tome.test", "key", "user")
    for route in ('"/tome-sync/device-info"', '"/tome-sync/files"',
                  '"/tome-sync/commands"', '"/tome-sync/commands/"',
                  '"/tome-sync/files/status?hash="'):
        assert route in lua
    assert '".tomesync-trash"' in lua


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
@pytest.mark.parametrize("auto", [False, True])
def test_renders_compile(tmp_path, auto):
    path = tmp_path / "main_impl.lua"
    path.write_text(_main_impl_lua("http://tome.test", "key", "user", auto_deliver=auto))
    out = subprocess.run(["luajit", "-e", f"assert(loadfile('{path}'))"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def _linkcmd_block() -> str:
    lua = _main_impl_lua("http://tome.test", "key", "user")
    start = lua.index("-- TOMESYNC_LINKCMD_BEGIN")
    end = lua.index("-- TOMESYNC_LINKCMD_END")
    return lua[start:end]


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_command_parser_ignores_non_command_bodies(tmp_path):
    """The HTML-200 case: apiRequest turns a non-JSON 2xx body into {}; a
    FastAPI error object or junk must not become a command either. Only real
    commands may reach the executor (and so a result POST)."""
    script = _linkcmd_block() + r'''
local function n(body) return #linkParseCommands(body) end
assert(n(nil) == 0, "nil")
assert(n("<!doctype html>") == 0, "raw html string")
assert(n({}) == 0, "html decoded to {}")
assert(n({ detail = "Not Found" }) == 0, "error object")
assert(n({ { id = 1 } }) == 0, "no op")
assert(n({ { op = "delete", args = { path = "x" } } }) == 0, "no id")
assert(n({ { id = 1, op = "format", args = {} } }) == 0, "unknown op")
assert(n({ { id = "../1", op = "delete", args = {} } }) == 0, "id with a path in it")
assert(n({ { id = 1, op = "delete", args = "x" } }) == 0, "args not a table")
assert(n({ { id = 1, op = "delete", args = { path = "a" } },
           { id = 2, op = "refresh" }, "junk", 7 }) == 2, "two real commands")
assert(n({ commands = { { id = "c-3", op = "mkdir", args = { path = "a" } } } }) == 1, "wrapped list")
print("ok")
'''
    path = tmp_path / "parse.lua"
    path.write_text(script)
    out = subprocess.run(["luajit", str(path)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr


def _linkpoll_block() -> str:
    lua = _main_impl_lua("http://tome.test", "key", "user")
    start = lua.index("-- TOMESYNC_LINKPOLL_BEGIN")
    end = lua.index("-- TOMESYNC_LINKPOLL_END")
    return lua[start:end]


def _run_lua(tmp_path, name, script):
    path = tmp_path / name
    path.write_text(script)
    out = subprocess.run(["luajit", str(path)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr + out.stdout


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_poll_seconds_decision(tmp_path):
    """Only a positive number polls. A real Tome sends no poll_seconds, and
    its SPA fallback decodes to {} - both mean no polling."""
    _run_lua(tmp_path, "poll.lua", _linkpoll_block() + r'''
local P = linkPollSeconds
assert(P(nil) == 0, "no body")
assert(P("<!doctype html>") == 0, "string body")
assert(P({}) == 0, "absent")
assert(P({ ok = true }) == 0, "absent with other fields")
assert(P({ poll_seconds = 0 }) == 0, "zero")
assert(P({ poll_seconds = -5 }) == 0, "negative")
assert(P({ poll_seconds = "5" }) == 0, "string")
assert(P({ poll_seconds = true }) == 0, "boolean")
assert(P({ poll_seconds = {} }) == 0, "table")
assert(P({ poll_seconds = 0/0 }) == 0, "NaN")
assert(P({ poll_seconds = math.huge }) == 0, "inf")
assert(P({ { id = 1 } }) == 0, "a bare list")
assert(P({ poll_seconds = 5 }) == 5, "five")
assert(P({ poll_seconds = 5.9 }) == 5, "floored")
assert(P({ poll_seconds = 0.5 }) == 2, "at least 2")
assert(P({ poll_seconds = 1e9 }) == 3600, "at most an hour")
assert(P({ commands = {}, poll_seconds = 5 }) == 5, "commands answer")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_tree_canon_is_stable(tmp_path):
    """The hashed text does not depend on walk order, and changes with any
    rename, size, mtime, sidecar or truncation change."""
    _run_lua(tmp_path, "canon.lua", _linkpoll_block() + r'''
local a = {
  { path = "book", type = "dir", mtime = 10 },
  { path = "book/A.epub", type = "file", size = 100, mtime = 11, sdr = true },
  { path = "book/B.epub", type = "file", size = 200, mtime = 12, sdr = false },
}
local b = { a[3], a[1], a[2] }
local C = linkTreeCanon
assert(C(a, false) == C(b, false), "order independent")
assert(C({}, false) == "", "empty tree")
local function with(i, k, v)
  local t = {}
  for j, e in ipairs(a) do
    local c = {}
    for kk, vv in pairs(e) do c[kk] = vv end
    if j == i then c[k] = v end
    t[j] = c
  end
  return C(t, false)
end
local base = C(a, false)
assert(with(2, "path", "book/A2.epub") ~= base, "rename")
assert(with(2, "size", 101) ~= base, "size")
assert(with(3, "mtime", 13) ~= base, "mtime")
assert(with(3, "sdr", true) ~= base, "sidecar")
assert(C(a, true) ~= base, "truncated")
assert(with(2, "mtime", 11.4) == base, "float mtime floored")
assert(base:find("\t", 1, true) and base:find("\n", 1, true), "tab and newline separators")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_command_ids_run_once(tmp_path):
    """A command id seen before is not run again; it is re-posted. 7 and "7"
    are one id, and a duplicate inside one answer runs once."""
    _run_lua(tmp_path, "split.lua", _linkpoll_block() + r'''
local done = { ["7"] = { ok = true, new_path = "x" } }
local fresh, again = linkSplitCommands({
  { id = 7, op = "rename" }, { id = "7", op = "rename" },
  { id = 8, op = "mkdir" }, { id = 8, op = "mkdir" }, { id = "c-9", op = "delete" },
}, done)
assert(#fresh == 2 and fresh[1].id == 8 and fresh[2].id == "c-9", "fresh: 8 and c-9")
assert(#again == 1 and again[1].id == 7, "again: 7 once")
local f2, a2 = linkSplitCommands({}, done)
assert(#f2 == 0 and #a2 == 0, "nothing")
print("ok")
''')


# Step six: TLS pinning. The fingerprint helpers are pure and run here; the
# handshake itself is proved against the app's listener in the emulator.

def _pin_block(fp: str = "") -> str:
    lua = _main_impl_lua("https://tome.test", "key", "user", server_fingerprint=fp)
    start = lua.index("-- TOMESYNC_PIN_BEGIN")
    end = lua.index("-- TOMESYNC_PIN_END")
    return lua[start:end]


def test_fingerprint_bake_defaults_empty_and_is_sanitised():
    assert 'local SERVER_FINGERPRINT = ""' in _main_impl_lua("http://tome.test", "key", "user")
    lua = _main_impl_lua("https://x", "k", "u", server_fingerprint='AB:cd"; os.exit() --')
    assert 'local SERVER_FINGERPRINT = "AB:cde"' in lua  # hex and colons only


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_fingerprint_comparison(tmp_path):
    hexfp = "146b389a234cb66cde7f96446afb3996a023ae06a05747d4871c9918774ddf9e"
    colon = ":".join(hexfp[i:i + 2] for i in range(0, 64, 2)).upper()
    off_by_one = hexfp[:-2] + "9f"
    script = _pin_block() + f'''
local hex, colon, off = "{hexfp}", "{colon}", "{off_by_one}"
assert(fingerprintMatches(hex, colon), "lower hex vs upper colon")
assert(fingerprintMatches(colon, hex), "colon vs hex")
assert(fingerprintMatches(colon:lower(), hex:upper()), "lower colon vs upper hex")
assert(fingerprintMatches(colon:gsub(":", " "), hex), "spaces")
assert(not fingerprintMatches(hex, off), "last byte off by one")
assert(not fingerprintMatches(hex, ""), "empty pin")
assert(not fingerprintMatches("", ""), "both empty")
assert(not fingerprintMatches(nil, hex), "no certificate")
assert(not fingerprintMatches(hex:sub(1, 62), hex:sub(1, 62)), "short")
assert(not fingerprintMatches(hex:sub(1, 63) .. "g", hex:sub(1, 63) .. "g"), "not hex")
assert(normFingerprint("") == nil and normFingerprint(nil) == nil, "empty is no pin")
assert(shortFingerprint(hex, 8) == "14:6B:38:9A:23:4C:B6:6C", shortFingerprint(hex, 8))
assert(shortFingerprint(hex) == colon, "full")
assert(shortFingerprint("junk", 8) == "?", "junk")
print("ok")
'''
    path = tmp_path / "pin.lua"
    path.write_text(script)
    out = subprocess.run(["luajit", str(path)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr + out.stdout


def test_no_pin_leaves_tls_untouched():
    """Without a pin, tlsCreateFor returns nil, so http.request picks LuaSec's
    own https create exactly as before (real Tome servers)."""
    lua = _main_impl_lua("https://tome.test", "key", "user")
    body = lua[lua.index("local function tlsCreateFor(base)"):]
    body = body[:body.index("\nend\n")]
    assert 'if not pin then return nil end' in body
    assert 'match("^https://") then return nil end' in body
