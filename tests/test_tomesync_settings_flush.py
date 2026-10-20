"""Connection settings survive a crash, run under luajit.

KOReader writes G_reader_settings to disk only on a clean exit. The plugin
saved a fresh pairing (API key + account) without flushing, so a crash
before the next clean exit lost it and the device asked to pair again (seen
on a Kindle in issue #259). The real pairing, string-setting and reset
functions are cut out of the generated impl and run against a settings fake
that keeps what is in memory apart from what was flushed to disk; the tests
check the disk, i.e. what a crash right afterwards would leave.
"""
import json
import shutil
import subprocess

import pytest

from backend.api.tome_sync import _main_impl_lua


def _cut(lua: str, header: str) -> str:
    start = lua.index(header)
    return lua[start:lua.index("\nend\n", start) + 5]


def _functions() -> str:
    lua = _main_impl_lua("http://main.example:8080", "tk_baked", "baked_user")
    return "".join(_cut(lua, h) for h in (
        "function TomeSync:_editStringSetting(",
        "function TomeSync:_resetConnection(",
        "function TomeSync:_signInWithCode(",
    ))


# `mem` is G_reader_settings in memory, `disk` what the last flush wrote.
# Scheduled callbacks run at once; the dialog fake records its buttons so a
# test can press Save.
_HARNESS = r"""
local mem, disk = {}, {}
local function copy(t) local c = {} for k, v in pairs(t) do c[k] = v end return c end
G_reader_settings = {
    readSetting = function(_, k) return mem[k] end,
    saveSetting = function(_, k, v) mem[k] = v end,
    delSetting  = function(_, k) mem[k] = nil end,
    flush       = function() disk = copy(mem) end,
}
UIManager = { show = function() end, close = function() end,
              scheduleIn = function(_, _, fn) fn() end }
InfoMessage = { new = function(_, t) return t end }
local dialog
InputDialog = { new = function(_, t)
    dialog = t
    t.onShowKeyboard = function() end
    t.getInputText = function() return t.typed end
    return t
end }
NetworkMgr = { isConnected = function() return true end }
local function resetBackoff() end
local function deviceName() return "Kindle" end
local LinkSession = { deviceID = function() return "00000000-0000-4000-8000-000000000000" end }
-- The TLS policy is tested on its own (test_tomesync_reader_link); http here.
local TLS = { trustThen = function(fn) return fn() end }
local logger = { info = function() end, warn = function() end, dbg = function() end }
local function username() return mem.tomesync_username or "baked_user" end
local function serverURL() return mem.tomesync_server_url or "http://main.example:8080" end
local responses = {
    ["/auth/quick-connect/initiate"] = { code = "ABC123", poll_token = "pt" },
    ["/auth/quick-connect/poll"]     = { status = "authorized", access_token = "jwt" },
    ["/plugin/api-keys"]             = { key = "tk_paired" },
    ["/auth/me"]                     = { username = "reader" },
}
local function apiRequest(_, path) return responses[path] end
TomeSync = {}
local self = setmetatable({}, { __index = TomeSync })
"""

_TAIL = r"""
SCENARIO()
local keys = {}
for k, v in pairs(disk) do keys[#keys + 1] = string.format("%q:%q", k, tostring(v)) end
table.sort(keys)
print("{" .. table.concat(keys, ",") .. "}")
"""


def _disk_after(scenario: str) -> dict:
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    src = (_HARNESS + _functions()
           + "function SCENARIO()\n" + scenario + "\nend\n" + _TAIL)
    proc = subprocess.run([luajit, "-"], input=src, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_pairing_is_on_disk_right_away():
    assert _disk_after("self:_signInWithCode()") == {
        "tomesync_api_key": "tk_paired",
        "tomesync_username": "reader",
    }


def test_saved_server_url_is_on_disk_right_away():
    assert _disk_after(r"""
        self:_editStringSetting("tomesync_server_url", "Server URL", "http://main.example:8080")
        dialog.typed = " https://tome.example.org "
        dialog.buttons[1][2].callback()
    """) == {"tomesync_server_url": "https://tome.example.org"}


def test_reset_connection_is_on_disk_right_away():
    assert _disk_after(r"""
        mem.tomesync_server_url = "https://tome.example.org"
        mem.tomesync_api_key = "tk_paired"
        mem.tomesync_username = "reader"
        mem.tomesync_auto_check = true
        G_reader_settings:flush()
        self:_resetConnection()
    """) == {"tomesync_auto_check": "true"}


def _first_dialog(kind):
    """The text of the pairing dialog when initiate answers with server_kind."""
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    harness = _HARNESS.replace(
        "UIManager = { show = function() end,",
        "SHOWN = {}\nUIManager = { show = function(_, w) SHOWN[#SHOWN + 1] = w.text end,")
    setup = "" if kind is None else f'responses["/auth/quick-connect/initiate"].server_kind = "{kind}"\n'
    src = (harness + _functions() + "function SCENARIO()\n" + setup
           + "self:_signInWithCode()\nend\nSCENARIO()\nio.write(SHOWN[1])\n")
    proc = subprocess.run([luajit, "-"], input=src, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_pairing_dialog_names_the_phone_only_for_tome_reader():
    phone = _first_dialog("tome-reader")
    assert "Pairing code:  ABC123" in phone
    assert "Tome Reader" in phone and "Settings > Readers" in phone
    assert "Quick Connect" not in phone
    for tome in (_first_dialog(None), _first_dialog("tome")):
        assert "Settings > Security > Quick Connect" in tome
        assert "Tome Reader" not in tome
