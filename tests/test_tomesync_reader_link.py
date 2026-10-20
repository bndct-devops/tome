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


def test_http_leaves_tls_untouched():
    """http: tlsCreateFor returns nil, so http.request runs exactly as before.
    https always goes through the policy's create (build 56)."""
    lua = _main_impl_lua("https://tome.test", "key", "user")
    body = lua[lua.index("local function tlsCreateFor(base)"):]
    body = body[:body.index("\nend\n")]
    assert 'match("^https://") then return nil end' in body
    assert "TLS.decision(" in body


# Build 56: the TLS policy for ordinary Tome servers, as one decision table.
@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_tls_policy_decision_table(tmp_path):
    hexfp = "146b389a234cb66cde7f96446afb3996a023ae06a05747d4871c9918774ddf9e"
    other = hexfp[:-2] + "9f"
    _run_lua(tmp_path, "policy.lua", _pin_block() + f'''
local pin, other = "{hexfp}", "{other}"
local D = TLS.decision
-- http: no TLS at all, whatever else is set
assert(D({{ https = false, pin = pin, got = other }}) == "plain", "http")
-- https + pin, certificate is the pin
assert(D({{ https = true, pin = pin, got = pin }}) == "pinned", "pin ok")
assert(D({{ https = true, pin = pin:upper(), got = pin, ca = false }}) == "pinned", "pin ok, no CA")
-- https + pin, another certificate: the reader link refuses, a Tome server asks again
assert(D({{ https = true, pin = pin, got = other, link = true }}) == "refuse", "link mismatch")
assert(D({{ https = true, pin = pin, got = nil, link = true }}) == "refuse", "link, no certificate")
assert(D({{ https = true, pin = pin, got = other }}) == "ask", "server mismatch asks again")
-- build 57: a pinned ordinary server whose new certificate is CA-valid for its
-- name (a Let's Encrypt renewal behind an IP-pinned setup): the pin goes
assert(D({{ https = true, pin = pin, got = other, ca = true }}) == "unpin", "CA renewal unpins")
assert(D({{ https = true, pin = pin, got = other, ca = true, baked = true }}) == "ask", "a baked pin beats the CA")
assert(D({{ https = true, pin = pin, got = other, ca = true, link = true }}) == "refuse", "the link never unpins")
assert(D({{ https = true, pin = pin, got = other, ca = false }}) == "ask", "not CA-valid: ask as before")
assert(D({{ https = true, pin = pin, got = other, ca = nil }}) == "ask", "no bundle: ask as before")
assert(D({{ https = true, pin = pin, got = nil, ca = true }}) == "ask", "no certificate: ask")
assert(D({{ https = true, pin = pin, got = pin, ca = true }}) == "pinned", "still the pin: pinned")
-- https, no pin
assert(D({{ https = true, got = pin, ca = true }}) == "ca", "CA ok")
assert(D({{ https = true, got = pin, ca = false }}) == "ask", "CA failed")
assert(D({{ https = true, got = pin, ca = nil }}) == "ask", "no CA bundle")
assert(D({{ https = true, pin = "", got = pin, ca = true }}) == "ca", "empty pin is no pin")
assert(D({{ https = true, pin = "junk", got = pin, ca = false }}) == "ask", "junk pin is no pin")
-- the host name check for a CA-verified certificate
local H = TLS.hostMatches
assert(H("tome.example.com", {{ "tome.example.com" }}), "exact")
assert(H("Tome.Example.com.", {{ "tome.example.COM" }}), "case, trailing dot")
assert(H("tome.example.com", {{ "*.example.com" }}), "wildcard")
assert(not H("a.tome.example.com", {{ "*.example.com" }}), "wildcard is one label")
assert(not H("example.com", {{ "*.example.com" }}), "wildcard needs a label")
assert(not H("evil.com", {{ "*.com" }}), "no wildcard for a whole TLD")
assert(not H("10.0.0.5", {{ "*.0.0.5" }}), "no wildcard for an IP")
assert(H("192.168.1.10", {{ "nas.local", "192.168.1.10" }}), "IP SAN")
assert(not H("tome.example.com", {{ "other.example.com" }}), "another name")
assert(not H("tome.example.com", {{}}), "no names")
print("ok")
''')


def test_tls_policy_wiring():
    lua = _impl()
    # background: an untrusted certificate is no miss (no back-off), logged once where found
    net = lua[lua.index("-- TOMESYNC_NET_BEGIN"):lua.index("-- TOMESYNC_NET_END")]
    assert "if code == TLS.UNTRUSTED then" in net
    assert "if not missed then return ok, code end" in net
    # every user action asks first; the menu asks without network, once
    wc = lua[lua.index("local function whenConnected(fn)"):]
    wc = wc[:wc.index("\nend\n")]
    assert "TLS.trustThen(fn)" in wc
    assert "TLS.trustThen(nil, { no_network = true, once = true })" in lua
    assert "return TLS.trustThen(function() self:_signInWithCode(true) end)" in lua
    # Trust pins it
    trust = lua[lua.index("function TLS.trustThen(fn, opts)"):]
    trust = trust[:trust.index("\nend\n")]
    assert 'saveSetting("tomesync_server_fingerprint", shortFingerprint(digest))' in trust
    assert "G_reader_settings:flush()" in trust
    # build 57: the CA renewal path unpins in both places a decision is made
    assert "TLS.unpin(base, pin, probe.digest)" in trust
    create = lua[lua.index("local function tlsCreateFor(base)"):]
    create = create[:create.index("\nend\n")]
    assert "TLS.unpin(base, pin, got)" in create
    assert "local cafile = (not link) and TLS.bundle() or nil" in create
    unpin = lua[lua.index("function TLS.unpin(base, pin, got)"):]
    unpin = unpin[:unpin.index("\nend\n")]
    assert 'delSetting("tomesync_server_fingerprint")' in unpin


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
-- build 57: FAT drops a trailing dot or space, so "run.sh." would land as run.sh
assert(refused("run.sh."), "run.sh.")
assert(refused("run.sh "), "run.sh + space")
assert(refused("evil.koplugin./main.lua"), "a folder with a trailing dot")
assert(refused("books/Series./x.epub"), "a trailing dot in a middle part")
assert(refused("books/x.epub "), "a trailing space")
assert(refused("x.lua\t"), "a trailing tab")
assert(P("books/Vol. 1.epub") == nil, "a dot inside stays allowed")
assert(P(" lead space.epub") == nil, "a leading space is not refused here")
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


# ── Build 55: one reader, the phone's wording, link hosts ────────────────────

def test_build_55_device_id_wording_and_probe_rules():
    assert TOMESYNC_PLUGIN_BUILD >= 55
    lua = _main_impl_lua()
    assert "local BUILD           = 59" in lua
    # A persistent id, sent in device-info and in the initiate body.
    assert "device_id    = LinkSession.deviceID()," in lua
    assert "{ device_id = LinkSession.deviceID(), device = deviceName() }" in lua
    assert 'saveSetting("tomesync_device_id", id)' in lua
    # The phone's wording only when the server says it is Tome Reader.
    assert 'resp.server_kind == "tome-reader"' in lua
    assert "Settings > Readers" in lua
    assert "Settings > Security > Quick Connect" in lua
    # Never a probe without a pin; the pinned create is used for each host.
    probe = lua[lua.index("local function linkProbeHosts"):lua.index("-- The pass: device info")]
    assert probe.index("if not pinnedFingerprint() then") < probe.index("http.request")
    assert "create  = tlsCreateFor(host)," in probe
    assert "socketutil:set_timeout(2, 4)" in probe


def test_device_id_is_a_uuid_made_once(tmp_path):
    import shutil, subprocess
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    lua = _main_impl_lua()
    fn = lua[lua.index("function LinkSession.deviceID()"):lua.index("local function idleCapSeconds")]
    src = """
local settings, flushed = {}, 0
G_reader_settings = { readSetting = function(_, k) return settings[k] end,
                      saveSetting = function(_, k, v) settings[k] = v end,
                      flush = function() flushed = flushed + 1 end }
local logger = { info = function() end }
local LinkSession = {}
""" + fn + """
local a = LinkSession.deviceID()
local b = LinkSession.deviceID()
assert(a == b, "same id twice")
assert(flushed == 1, "saved once")
assert(a:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-4%x%x%x%-[89ab]%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"), a)
settings.tomesync_device_id = "junk"
assert(LinkSession.deviceID() ~= "junk", "a malformed id is replaced")
print("ok")
"""
    out = subprocess.run([luajit, "-"], input=src, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr + out.stdout


# ── Build 56: dot titles, the trash's age ────────────────────────────────────

@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_delivery_names_lose_their_leading_dots(tmp_path):
    _run_lua(tmp_path, "dots.lua", _linkguard_block() + r'''
local S = linkSafeName
-- getSafeFilename(".hack//SIGN.epub") gives ".hack__SIGN.epub"
assert(S(".hack__SIGN.epub", "42", "epub") == "hack__SIGN.epub", S(".hack__SIGN.epub", "42", "epub"))
assert(S(".hack__SIGN", "42") == "hack__SIGN", "a folder")
-- "..." as a title: getSafeFilename gives ".epub"
assert(S(".epub", "42", "epub") == "42.epub", S(".epub", "42", "epub"))
assert(S("...", "42") == "42", "a folder of dots")
assert(S(" . .x.epub", "42", "epub") == "x.epub", "dots and spaces")
assert(S("Vol. 1 - A.epub", "42", "epub") == "Vol. 1 - A.epub", "a dot inside stays")
assert(S("A.EPUB", "42", "epub") == "A.EPUB", "extension case kept")
assert(S(".kepub.epub", "42", "kepub.epub") == "42.kepub.epub", "two-part extension")
-- build 57: a folder loses trailing dots and spaces; a file stem keeps them
assert(S("Acme Inc.", "42") == "Acme Inc", S("Acme Inc.", "42"))
assert(S("Series . ", "42") == "Series", "dots and spaces at the end")
assert(S("Mr..epub", "42", "epub") == "Mr..epub", "a stem's trailing dot stays (the part ends in epub)")
assert(linkPathRefused("book/Acme Inc/Mr..epub") == nil, "cleaned path allowed")
-- the cleaned names pass the guard; a rename to a dot name is still refused
assert(linkPathRefused("book/hack__SIGN/hack__SIGN.epub") == nil, "clean path allowed")
assert(linkPartRefused(".foo") ~= nil, "rename to .foo refused")
print("ok")
''')


def _linktrash_block() -> str:
    lua = _impl()
    start = lua.index("-- TOMESYNC_LINKTRASH_BEGIN")
    end = lua.index("-- TOMESYNC_LINKTRASH_END")
    return "TomeSync = {}\n" + lua[start:end]


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_trash_purge_by_age_and_bound(tmp_path):
    _run_lua(tmp_path, "trash.lua", _linktrash_block() + r'''
local P = TomeSync._linkTrashPlan
local now, day, MB = 1000 * 86400, 86400, 1048576
local function names(list) local o = {} for i, it in ipairs(list) do o[i] = it.name end return table.concat(o, ",") end
local three = {
  { name = "old.epub", t = now - 40 * day, bytes = 1 },
  { name = "mid.epub", t = now - 20 * day, bytes = 1 },
  { name = "new.epub", t = now - 1 * day,  bytes = 1 },
}
local out, by_age = P(three, nil, now, 30, 50, 200 * MB)
assert(names(out) == "old.epub" and by_age == 1, names(out))
-- 0 = never by age
out, by_age = P(three, nil, now, 0, 50, 200 * MB)
assert(#out == 0 and by_age == 0, "days 0 purges nothing")
-- the newest item is never purged, even when everything is old
local all_old = { { name = "a", t = now - 90 * day, bytes = 1 }, { name = "b", t = now - 80 * day, bytes = 1 } }
out = P(all_old, nil, now, 30, 50, 200 * MB)
assert(names(out) == "a", names(out))
-- the one just trashed is kept even if its mtime is older
out = P(all_old, "a", now, 30, 50, 200 * MB)
assert(names(out) == "b", names(out))
-- the bound still applies, oldest first, after the age
local big = {
  { name = "x", t = now - 3 * day, bytes = 150 * MB },
  { name = "y", t = now - 2 * day, bytes = 60 * MB },
  { name = "z", t = now - 1 * day, bytes = 10 * MB },
}
out, by_age = P(big, nil, now, 30, 50, 200 * MB)
assert(names(out) == "x" and by_age == 0, names(out))
-- items bound
local many = {}
for i = 1, 53 do many[i] = { name = string.format("n%02d", i), t = now - (60 - i) , bytes = 1 } end
out = P(many, nil, now, 30, 50, 200 * MB)
assert(names(out) == "n01,n02,n03", names(out))
assert(#P({}, nil, now, 30, 50, 200 * MB) == 0, "empty")
print("ok")
''')


# ── Build 57: the heartbeat after a failed poll, the tree walk, config.lua ──

@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_heartbeat_decision(tmp_path):
    _run_lua(tmp_path, "hb.lua", _linkpoll_block() + r"""
local A, M = LinkHB.after, LinkHB.minutes
local now = 1000000
-- an answer with poll_seconds always polls, heartbeat or not
local a, b = A({ secs = 5, now = now, minutes = 30 })
assert(a == "poll" and b == 5, "poll")
a, b = A({ secs = 5, now = now, minutes = 30, hb_until = now + 60 })
assert(a == "poll" and b == 5, "heartbeat answered: poll again")
-- a poll that got no answer opens the window
a, b = A({ secs = 0, noanswer = true, polling = true, now = now, minutes = 30 })
assert(a == "heartbeat" and b == now + 1800, "window opens for 30 min")
-- inside the window a miss keeps it, the end does not move
a, b = A({ secs = 0, noanswer = true, hb_until = now + 100, now = now, minutes = 30 })
assert(a == "heartbeat" and b == now + 100, "window kept")
-- the window over: stop until the next sync moment
a, b = A({ secs = 0, noanswer = true, hb_until = now, now = now, minutes = 30 })
assert(a == "stop" and b:find("heartbeat over"), "window over")
-- no poll before: a real Tome that did not answer gets no heartbeat
a = A({ secs = 0, noanswer = true, polling = false, now = now, minutes = 30 })
assert(a == "stop", "no heartbeat without a poll")
-- off switch
a = A({ secs = 0, noanswer = true, polling = true, now = now, minutes = 0 })
assert(a == "stop", "0 = off")
-- the server answered but did not ask: stop
a, b = A({ secs = 0, noanswer = false, hb_until = now + 100, now = now, minutes = 30 })
assert(a == "stop" and b == "server answered without a poll", "answer without poll")
a = A({ secs = 0, noanswer = false, polling = true, now = now, minutes = 30 })
assert(a == "stop", "plain stop")
-- the setting
assert(M(nil) == 30 and M("5") == 30 and M(-1) == 30 and M(0/0) == 30, "defaults")
assert(M(0) == 0 and M(10) == 10 and M(7.9) == 7, "values")
assert(LinkHB.EVERY == 60 and LinkHB.NET.block == 2 and LinkHB.NET.total == 2 and LinkHB.NET.no_backoff, "every 60 s, 2 s")
print("ok")
""")


def test_heartbeat_wiring():
    lua = _impl()
    net = lua[lua.index("-- TOMESYNC_NET_BEGIN"):lua.index("-- TOMESYNC_NET_END")]
    assert "if spec.no_backoff then return ok, code end\n    noteNetFailure()" in net
    api = lua[lua.index("local function apiRequest(method, path, body, opts)"):]
    api = api[:api.index("\nend\n")]
    assert "if inBackoff() and not (opts and opts.no_backoff) then" in api
    pas = lua[lua.index("function TomeSync:_linkPass(light, hb)"):]
    pas = pas[:pas.index("\nend\n")]
    # heartbeat: commands GET only (no device-info, no tree, no probe)
    assert "local probe = not light and not hb and" in pas
    assert "if link_on and not hb and (not light" in pas
    assert "if link_on and root and not reading and (not hb or delivered_now) and" in pas
    assert 'tomesync_link_heartbeat_minutes' in lua
    stop = lua[lua.index("local function linkStopPoll(why)"):]
    stop = stop[:stop.index("\nend\n")]
    assert "LinkHB.till = nil" in stop


def test_tree_walk_failure_sends_nothing():
    lua = _impl()
    tree = lua[lua.index("local function linkFileTree(root)"):]
    tree = tree[:tree.index("\nend\n")]
    assert 'if root_failed then return nil, "cannot list "' in tree
    send = lua[lua.index("local function linkSendTree(root, light)"):]
    send = send[:send.index("\nend\n")]
    i_fail = send.index("if not entries then")
    assert i_fail < send.index("/tome-sync/files/status") and i_fail < send.index('"PUT", "/tome-sync/files"')


# ── Build 58: deliveries on light passes, heartbeat in the file manager, 401 ──

@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_light_pass_inbox_decision(tmp_path):
    _run_lua(tmp_path, "inbox.lua", _linkpoll_block() + r"""
local C, I = LinkHB.inboxCount, LinkHB.inbox
assert(C(nil) == 0 and C({}) == 0 and C({ inbox = 0 }) == 0 and C({ inbox = -1 }) == 0, "none")
assert(C({ inbox = "1" }) == 0 and C({ inbox = 0/0 }) == 0 and C({ inbox = math.huge }) == 0, "junk")
assert(C({ commands = {}, inbox = 2, poll_seconds = 5 }) == 2 and C({ inbox = 1.7 }) == 1, "count")
-- file manager, light pass, something waiting: deliver now
assert(I({ auto = true, full = false, n = 1, reading = false }) == "deliver", "deliver now")
-- in the reader: only noted
assert(I({ auto = true, full = false, n = 1, reading = true }) == "note", "note in the reader")
-- nothing waiting: nothing on a light pass
assert(I({ auto = true, full = false, n = 0, reading = false }) == "none", "nothing waiting")
-- a full pass asks the inbox itself, as before, wherever KOReader is
assert(I({ auto = true, full = true, n = 0, reading = true }) == "deliver", "full pass")
-- auto-delivery off, no host answered, or a forgotten reader: nothing
assert(I({ auto = false, full = false, n = 3, reading = false }) == "none", "auto off")
assert(I({ auto = false, full = true, n = 0 }) == "none", "auto off, full")
assert(I({ auto = true, full = true, missed = true }) == "none", "missed")
assert(I({ auto = true, full = false, n = 1, repair = true }) == "none", "repair")
print("ok")
""")


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_heartbeat_only_in_the_file_manager(tmp_path):
    _run_lua(tmp_path, "hbwhere.lua", _linkpoll_block() + r"""
local W = LinkHB.where
assert(W("heartbeat", false) == "heartbeat", "file manager: knock")
assert(W("heartbeat", true) == "paused", "reader: paused")
assert(W("poll", true) == "poll" and W("stop", true) == "stop", "poll and stop unchanged")
-- the window clock runs while reading: once it is over, the close pass stops
local now = 1000000
local a = LinkHB.after({ secs = 0, noanswer = true, hb_until = now - 1, now = now, minutes = 30 })
assert(a == "stop", "window over while reading")
a = LinkHB.after({ secs = 0, noanswer = true, hb_until = now + 60, now = now, minutes = 30 })
assert(W(a, false) == "heartbeat", "window still open after the close")
print("ok")
""")


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_repair_on_401(tmp_path):
    _run_lua(tmp_path, "repair.lua", _linkpoll_block() + r"""
local R = LinkHB.repair
assert(R(401, '{"detail":"Device id mismatch"}', true), "mismatch")
assert(R(401, '{"detail":"Invalid API key: unknown key"}', true), "unknown key")
assert(R(401, '{"detail":"UNKNOWN KEY"}', true), "any case")
-- a real Tome (never asked for a poll) or another answer: nothing
assert(not R(401, '{"detail":"Device id mismatch"}', false), "not a link")
assert(not R(401, '{"detail":"Invalid API key"}', true), "plain invalid key")
assert(not R(403, '{"detail":"unknown key"}', true), "not 401")
assert(not R(401, nil, true) and not R("timeout", nil, true), "no body")
assert(LinkHB.REPAIR_TEXT == "This phone no longer recognises the reader. Pair again in Tome Reader.", "text")
print("ok")
""")


def test_build_58_wiring():
    lua = _impl()
    assert "local BUILD           = 59" in lua
    api = lua[lua.index("local function apiRequest(method, path, body, opts)"):]
    api = api[:api.index("\nend\n")]
    assert "return nil, code, resp_body" in api
    pas = lua[lua.index("function TomeSync:_linkPass(light, hb)"):]
    pas = pas[:pas.index("\nend\n")]
    assert "inbox_n = LinkHB.inboxCount(list)" in pas
    assert "self:_deliverInbox(items, true)" in pas
    assert 'self:_linkRepair(code, raw, "device-info")' in pas
    assert 'self:_linkRepair(code, raw, "commands")' in pas
    assert "and not link_off.all and not repair" in pas
    arm = lua[lua.index("function TomeSync:_linkArmPoll(secs, why, noanswer)"):]
    arm = arm[:arm.index("\nend\n\n")]
    assert "act = LinkHB.where(act, linkReading())" in arm
    i_task = arm.index("link_poll_task = function()")
    assert arm.index("if linkReading() then", i_task) < arm.index("pcall(me._linkPass, me, true, true)")
    # no link host answered is no answer (the heartbeat window stays open), and
    # no host retry pass while the window is open (it would drop the heartbeat)
    assert "if not probed then link_on, missed, noanswer = false, true, true end" in pas
    assert "if link_host_missed and not link_task and not LinkHB.till then" in lua


# ── Build 59: KOReader settings from the phone ──────────────────────────────

def _linkset_block() -> str:
    lua = _impl()
    start = lua.index("-- TOMESYNC_LINKSET_BEGIN")
    end = lua.index("-- TOMESYNC_LINKSET_END")
    return lua[start:end]


ALL_CAPS = ("{ frontlight = true, natural_light = true, eink = true, suspend = true, standby = true,"
            " power_off = true, wifi_restore = true, wifi_powersave = true, touch = true }")


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_setting_whitelist_validation(tmp_path):
    """Only curated keys, the right type, inside the range; never clamped."""
    _run_lua(tmp_path, "set.lua", _linkset_block() + r'''
local ctx = { caps = ''' + ALL_CAPS + r''', fonts = { "Noto Serif", "Literata" },
              limits = { frontlight = { min = 0, max = 10 } } }
local function ok(k, v, c) local a, b = LinkSet.check(k, v, c or ctx); assert(a == true, k .. ": " .. tostring(b)); return b end
local function no(k, v, want, c)
    local a, b = LinkSet.check(k, v, c or ctx)
    assert(a == false, tostring(k) .. " should be refused")
    assert(type(b) == "string" and b:find(want, 1, true), tostring(k) .. ": " .. tostring(b))
end
-- unknown and missing keys
no("format_card", true, "unknown setting: format_card")
no("home_dir", "/", "unknown setting")
no("tomesync_api_key", "x", "unknown setting")
no(nil, true, "missing key")
-- types
assert(ok("night_mode", true) == true and ok("night_mode", false) == false)
no("night_mode", "true", "expected true or false")
no("night_mode", 1, "expected true or false")
no("font_size", "22", "expected a number")
no("font_size", 0/0, "expected a number")
no("font_size", math.huge, "expected a number")
no("line_spacing", 100.5, "expected a whole number")
no("sleep_screen", 3, "expected one of")
no("sleep_message", false, "expected text")
no("font_face", 7, "expected a font name")
-- ranges (refused, not clamped)
assert(ok("font_size", 22.5) == 22.5)
no("font_size", 22.25, "steps of 0.5")
no("font_size", 11.5, "out of range (12..255)")
no("font_size", 256, "out of range")
assert(ok("line_spacing", 50) == 50 and ok("line_spacing", 200) == 200)
no("line_spacing", 49, "out of range (50..200)")
no("margin_sides", -1, "out of range (0..140)")
no("margin_top", 141, "out of range")
assert(ok("refresh_every", -1) == -1 and ok("refresh_every", 0) == 0)
no("refresh_every", -2, "out of range (-1..200)")
assert(ok("sleep_after", -1) == -1 and ok("sleep_after", 60) == 60)
no("sleep_after", 59, "out of range (60..86400, or -1 for off)")
no("sleep_after", 0, "out of range")
no("shutdown_after", 299, "out of range")
no("standby_after", 901, "out of range")
-- the frontlight's range is the device's, not the table's
assert(ok("frontlight", 10) == 10)
no("frontlight", 11, "out of range (0..10)")
assert(ok("frontlight", 24, { caps = { frontlight = true }, limits = { frontlight = { min = 0, max = 24 } } }) == 24)
no("warmth", 101, "out of range (0..100)")
-- enums and fonts
assert(ok("sleep_screen", "cover") == "cover")
no("sleep_screen", "random_image", "not one of cover, bookstatus, disable")
assert(ok("font_face", "Literata") == "Literata")
no("font_face", "Comic Sans", "no font named Comic Sans")
no("font_face", "Literata", "font list is not known", { caps = {} })
no("sleep_message", string.rep("x", 301), "longer than 300")
no("sleep_message", "a\0b", "NUL")
-- capabilities: a key the reader lacks is refused
no("frontlight", 3, "has no frontlight", { caps = {} })
no("warmth", 3, "has no warmth control", { caps = { frontlight = true } })
no("standby_after", 10, "has no standby", { caps = {} })
no("auto_disable_wifi", true, "no Wi-Fi power saving", { caps = {} })
assert(ok("night_mode", true, { caps = {} }) == true, "night mode needs nothing")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_setting_applies_notes(tmp_path):
    """Every key says when it takes effect, names its KOReader setting (or a
    live device value), and the notes are the ones the app knows."""
    _run_lua(tmp_path, "applies.lua", _linkset_block() + r'''
local want = {
    night_mode = "now", frontlight = "now", warmth = "now", refresh_every = "now",
    sleep_after = "now", standby_after = "now", shutdown_after = "now",
    sleep_screen = "now", sleep_message_on = "now", sleep_message = "now",
    restore_wifi = "now", auto_disable_wifi = "restart",
    font_face = "next book", font_size = "next book", line_spacing = "next book",
    margin_sides = "next book", margin_top = "next book", margin_bottom = "next book",
    invert_page_turns = "next book",
}
local n = 0
for _, s in ipairs(LinkSet.KEYS) do
    n = n + 1
    assert(want[s.key] == s.applies, s.key .. " applies " .. tostring(s.applies))
    assert(LinkSet.APPLIES[s.applies], s.key)
    assert(s.setting or s.live, s.key .. " has neither a setting nor a live value")
    assert(s.section, s.key .. " has no section")
end
for k in pairs(want) do assert(LinkSet.byKey[k], "missing " .. k) end
assert(n == 19, "19 keys, got " .. n)
-- the KOReader names (checked against its source)
local names = {
    night_mode = "night_mode", refresh_every = "full_refresh_count",
    sleep_after = "auto_suspend_timeout_seconds", standby_after = "auto_standby_timeout_seconds",
    shutdown_after = "autoshutdown_timeout_seconds", sleep_screen = "screensaver_type",
    sleep_message_on = "screensaver_show_message", sleep_message = "screensaver_message",
    restore_wifi = "auto_restore_wifi", auto_disable_wifi = "auto_disable_wifi",
    font_face = "cre_font", font_size = "copt_font_size", line_spacing = "copt_line_spacing",
    margin_sides = "copt_h_page_margins", margin_top = "copt_t_page_margin",
    margin_bottom = "copt_b_page_margin", invert_page_turns = "inverse_reading_order",
}
for k, v in pairs(names) do assert(LinkSet.byKey[k].setting == v, k) end
assert(LinkSet.byKey.margin_sides.pair == true, "L/R margins are a pair")
print("ok")
''')


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_settings_report_shape(tmp_path):
    """The report: capabilities, settings for the keys this reader has only,
    the key descriptions with ranges and applies, fonts, profiles."""
    _run_lua(tmp_path, "report.lua", _linkset_block() + r'''
local caps = { frontlight = true, natural_light = false, suspend = true, power_off = true, touch = true }
local r = LinkSet.report({ night_mode = true, frontlight = 4, warmth = 30, font_size = 22,
                           sleep_after = 900, not_a_key = 1 },
                         caps, { "Noto Serif" }, { { name = "Night", autoexec = {} } },
                         { frontlight = { min = 0, max = 24 } })
assert(r.capabilities == caps)
assert(r.settings.night_mode == true and r.settings.frontlight == 4 and r.settings.font_size == 22)
assert(r.settings.warmth == nil, "no warmth without natural_light")
assert(r.settings.not_a_key == nil, "only curated keys")
assert(r.keys.frontlight.min == 0 and r.keys.frontlight.max == 24, "device range")
assert(r.keys.warmth == nil and r.keys.refresh_every == nil and r.keys.standby_after == nil)
assert(r.keys.sleep_after.off == -1 and r.keys.sleep_after.min == 60 and r.keys.sleep_after.type == "seconds")
assert(r.keys.font_size.applies == "next book" and r.keys.night_mode.applies == "now")
assert(r.keys.sleep_screen.values[1] == "cover" and r.keys.sleep_screen.section == "sleep")
assert(r.keys.invert_page_turns ~= nil and r.keys.auto_disable_wifi == nil)
assert(r.fonts[1] == "Noto Serif" and r.profiles[1].name == "Night")
local empty = LinkSet.report(nil, nil, nil, nil, nil)
assert(next(empty.settings) == nil and empty.keys.night_mode and empty.keys.frontlight == nil)
print("ok")
''')


def test_build_59_wiring():
    lua = _impl()
    assert "local BUILD           = 59" in lua
    assert TOMESYNC_PLUGIN_BUILD == 59
    assert "set_setting = true, run_profile = true" in lua
    assert '"/tome-sync/reader-settings"' in lua
    assert 'if op == "set_setting" then return self:_linkSetSetting(args) end' in lua
    assert 'if op == "run_profile" then return self:_linkRunProfile(args) end' in lua
    # Live paths: KOReader's own events and the profiles plugin's own execute.
    for ev in ('"SetNightMode"', '"SetFlIntensity"', '"SetFlWarmth"', '"SetBothRefreshRates"'):
        assert ev in lua
    assert "p:onProfileExecute(name, { qm_show = false })" in lua
    assert "applies = res and applies or nil" in lua
    # The report: full passes and after a settings command; off on 404/405.
    assert "(not light or LinkSet.dirty)" in lua
    assert "link_off.reader_settings = true" in lua


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_parser_takes_settings_ops(tmp_path):
    _run_lua(tmp_path, "ops.lua", _linkcmd_block() + r'''
assert(#linkParseCommands({ commands = {
    { id = 1, op = "set_setting", args = { key = "night_mode", value = true } },
    { id = 2, op = "run_profile", args = { name = "Night" } },
    { id = 3, op = "set_settings", args = {} } } }) == 2)
print("ok")
''')
