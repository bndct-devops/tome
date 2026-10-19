"""Reader link (plugin build 53): the bake keyword and the Lua it renders.

The routes themselves are not on Tome yet (the iOS app's listener serves them);
these tests pin the bake: auto-pickup stays off for every existing caller, the
reader link turns it on, and both renders are valid Lua.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD, _config_lua, _main_impl_lua

# The plugin's Lua is a file since build 54; blocks are read from it directly.
IMPL_PATH = Path(__file__).resolve().parent.parent / "backend" / "tomesync.koplugin" / "main_impl.lua"


def _impl() -> str:
    return IMPL_PATH.read_text(encoding="utf-8")


def test_build_is_53_or_later():
    assert TOMESYNC_PLUGIN_BUILD >= 53


def test_auto_deliver_defaults_off():
    assert "AUTO_DELIVER = false\n" in _config_lua("http://tome.test", "key", "user")
    assert "local DEFAULT_AUTO_DELIVER = PLUGIN_CONFIG.AUTO_DELIVER == true" in _impl()


def test_auto_deliver_baked_on():
    assert "AUTO_DELIVER = true\n" in _config_lua("http://tome.test", "key", "user", auto_deliver=True)


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
    impl = tmp_path / "main_impl.lua"
    impl.write_text(_main_impl_lua())
    config = tmp_path / "config.lua"
    config.write_text(_config_lua("http://tome.test", "key", "user", auto_deliver=auto))
    for path in (impl, config):
        out = subprocess.run(["luajit", "-e", f"assert(loadfile('{path}'))"],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def _linkcmd_block() -> str:
    lua = _impl()
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
    lua = _impl()
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
assert(P({ poll_seconds = 0.5 }) == 5, "at least 5")
assert(P({ poll_seconds = 2 }) == 5, "2 is floored to 5")
assert(P({ poll_seconds = 7 }) == 7, "seven")
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
    lua = _impl()
    start = lua.index("-- TOMESYNC_PIN_BEGIN")
    end = lua.index("-- TOMESYNC_PIN_END")
    return lua[start:end]


def test_fingerprint_bake_defaults_empty_and_is_sanitised():
    assert 'SERVER_FINGERPRINT = ""\n' in _config_lua("http://tome.test", "key", "user")
    lua = _config_lua("https://x", "k", "u", server_fingerprint='AB:cd"; os.exit() --')
    assert 'SERVER_FINGERPRINT = "AB:cde"\n' in lua  # hex and colons only


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


# Step seven: the refusal rules (a server must not be able to plant code on the
# device), case-insensitive storage, and the pin log. The pure parts run here;
# the identity checks (dev+ino, KOReader's folders) are proved in the emulator.

def _linkguard_block() -> str:
    lua = _impl()
    start = lua.index("-- TOMESYNC_LINKGUARD_BEGIN")
    end = lua.index("-- TOMESYNC_LINKGUARD_END")
    return lua[start:end]


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_refused_names(tmp_path):
    _run_lua(tmp_path, "guard.lua", _linkguard_block() + r'''
local P = linkPathRefused
local function refused(rel) return P(rel) ~= nil end
-- dot parts: hidden folders (Kobo's .adds holds KOReader), anywhere in the path
assert(refused(".adds/x"), ".adds")
assert(refused("books/.adds/koreader/patches"), "dot part deep")
assert(refused(".hidden.epub"), "dot file")
assert(refused("a/../b") and refused("./a"), ". and ..")
-- runnable names, any case, any part
assert(refused("x.koplugin"), "koplugin")
assert(refused("plugins/evil.KOPLUGIN/main.lua"), "koplugin folder, any case")
assert(refused("x.lua") and refused("X.LUA"), "lua")
assert(refused("a/b/run.sh"), "sh")
assert(refused("dir.lua/book.epub"), "a folder named .lua")
-- the trash, in any case
assert(P(".tomesync-trash/x") == "path is inside the trash", P(".tomesync-trash/x"))
assert(P(".TomeSync-Trash/x") == "path is inside the trash", "trash, other case")
assert(P(".TOMESYNC-TRASH/y") == "path is inside the trash", "trash, upper case")
-- what stays allowed
assert(P("book/Series/Vol. 1.epub") == nil, "normal book")
assert(P("koreader/patches") == nil, "by name only: koreader/ is refused by identity, not here")
assert(P("lua guide.epub") == nil and P("notes.lua.epub") == nil, "lua not at the end")
assert(P("a.shx") == nil, "not .sh")
assert(linkPartRefused("") ~= nil, "empty name")
-- KOReader's folders by name, ignoring case
assert(linkUnderNoCase("/mnt/us/koreader/patches", "/mnt/us/koreader"), "under")
assert(linkUnderNoCase("/mnt/us/KOReader", "/mnt/us/koreader"), "equal, other case")
assert(not linkUnderNoCase("/mnt/us/koreader2/x", "/mnt/us/koreader"), "a sibling with a longer name")
assert(not linkUnderNoCase("/mnt/us/documents", "/mnt/us/koreader"), "elsewhere")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_case_only_rename_decision_and_stored_spelling(tmp_path):
    _run_lua(tmp_path, "case.lua", _linkguard_block() + r'''
assert(linkCaseOnly("Foo.epub", "foo.epub"), "case only")
assert(not linkCaseOnly("Foo.epub", "Foo.epub"), "same name is not a rename")
assert(not linkCaseOnly("Foo.epub", "Bar.epub"), "a real rename")
assert(not linkCaseOnly("Foo.epub", nil), "no new name")
local stored = {
  "/mnt/us/books/Foo.epub", "/mnt/us/books/Other.epub",
  "/mnt/us/Books/Series/A.epub", "/mnt/us/books/series/B.epub", "/mnt/us/books/Foo.epub",
}
local s = linkStoredSpellings(stored, "/mnt/us/books/foo.epub", false)
assert(#s == 1 and s[1] == "/mnt/us/books/Foo.epub", "the stored spelling, once")
assert(#linkStoredSpellings(stored, "/mnt/us/books/none.epub", false) == 0, "nothing stored")
local d = linkStoredSpellings(stored, "/mnt/us/books/series/", true)
assert(#d == 2 and d[1] == "/mnt/us/Books/Series/" and d[2] == "/mnt/us/books/series/", "both folder spellings")
assert(#linkStoredSpellings(stored, "/mnt/us/books/ser/", true) == 0, "a prefix of a name is not the folder")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_pin_mismatch_log_shows_both_ends(tmp_path):
    hexfp = "146b389a234cb66cde7f96446afb3996a023ae06a05747d4871c9918774ddf9e"
    tail = hexfp[:-2] + "9f"
    _run_lua(tmp_path, "edge.lua", _pin_block() + f'''
local a, b = edgeFingerprint("{hexfp}"), edgeFingerprint("{tail}")
assert(a == "14:6B:38:9A..77:4D:DF:9E", a)
assert(b == "14:6B:38:9A..77:4D:DF:9F", b)
assert(a ~= b, "a tail-only difference shows")
assert(edgeFingerprint("junk") == "?", "junk")
print("ok")
''')


def test_download_and_delivered_hardening_in_render():
    lua = _main_impl_lua("http://tome.test", "key", "user")
    assert '"/tome-sync/inbox/" .. urlEncode(tostring(item.id)) .. "/delivered"' in lua
    assert "local API_BODY_MAX = 8 * 1024 * 1024" in lua
    assert "TomeSync._linkDownloadRefused(base_dir, dest, ext, guarded)" in lua
    assert "LINK_TRASH_MAX_BYTES = 200 * 1024 * 1024" in lua and "LINK_TRASH_MAX_ITEMS = 50" in lua
