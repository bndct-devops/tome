"""Behaviour of the plugin's network layer (issue #249), run under luajit.

Every TomeSync request runs on KOReader's UI thread, so a server that does
not answer while the device is online froze the reader for the connect
timeout. The TOMESYNC_NET block of the generated impl (back-off, server
addresses, serverRequest) is cut out and executed with fakes for KOReader's
settings, http and socketutil plus a controllable clock, so these tests drive
the real Lua rather than pinning strings.
"""
import json
import shutil
import subprocess

import pytest

from backend.api.tome_sync import _main_impl_lua


def _net_block() -> str:
    lua = _main_impl_lua("http://main.example:8080", "tk_test", "tester")
    start = lua.index("-- TOMESYNC_NET_BEGIN")
    end = lua.index("-- TOMESYNC_NET_END")
    return lua[start:end]


# Fakes: settings table, a clock, http that answers per base URL from a
# script, and a call log. `hosts[url] = "up" | "down"`.
_HARNESS = r"""
local settings = {}
G_reader_settings = {
    readSetting = function(_, k) return settings[k] end,
    saveSetting = function(_, k, v) settings[k] = v end,
    delSetting  = function(_, k) settings[k] = nil end,
}
local now = 1000
local real_os = os
os = setmetatable({ time = function() return now end }, { __index = real_os })
local DEFAULT_SERVER_URL = "http://main.example:8080"
local function liveSetting(key, default)
    local v = G_reader_settings:readSetting(key)
    if v == nil or v == "" then return default end
    return v
end
local logger = { warn = function() end, dbg = function() end, info = function() end }
local socketutil = { set_timeout = function() end, reset_timeout = function() end }
local ltn12 = {
    source = { string = function(s) return s end },
    sink = { table = function(t) return function(c) if c then t[#t + 1] = c end return 1 end end },
}
local PIN_REFUSED = "certificate fingerprint mismatch"
local function tlsCreateFor() return nil end
local hosts, calls = {}, {}
local http = {
    request = function(req)
        local base = req.url:match("^(https?://[^/]+)")
        calls[#calls + 1] = base
        if hosts[base] == "up" then
            req.sink("ok")
            return 1, 200
        end
        return nil, "timeout"
    end,
}
"""

_PROBE = r"""
local function get(path)
    local chunks
    local ok, code = serverRequest({
        method = "GET", path = path, headers = {},
        sink = function() chunks = {} return ltn12.sink.table(chunks) end,
        block = 5, total = 45,
    })
    return ok, code
end
local out, n_out = {}, 0
local function emit(v) n_out = n_out + 1; out[n_out] = v end
local function json(v)
    if type(v) == "table" then
        local parts = {}
        local is_list = #v > 0 or next(v) == nil
        if is_list then
            for _, x in ipairs(v) do parts[#parts + 1] = json(x) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for k, x in pairs(v) do parts[#parts + 1] = string.format("%q:%s", k, json(x)) end
        return "{" .. table.concat(parts, ",") .. "}"
    elseif type(v) == "string" then return string.format("%q", v)
    elseif v == nil then return "null"
    else return tostring(v) end
end
"""


def _run(scenario: str) -> list:
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    tail = r"""
local parts = {}
for i = 1, n_out do parts[i] = json(out[i]) end
print("[" .. table.concat(parts, ",") .. "]")
"""
    src = _HARNESS + _net_block() + _PROBE + scenario + tail
    proc = subprocess.run([luajit, "-"], input=src, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_backoff_starts_after_the_first_miss_and_doubles_to_a_cap():
    out = _run(r"""
        hosts["http://main.example:8080"] = "down"
        for _ = 1, 7 do
            get("/api/x")
            emit(backoff_until - now)
            now = backoff_until   -- window over: the next request is the probe
        end
    """)
    assert out == [60, 120, 240, 480, 900, 900, 900]


def test_requests_inside_the_window_are_skipped_and_success_clears_it():
    out = _run(r"""
        hosts["http://main.example:8080"] = "down"
        get("/api/x")
        emit(inBackoff())
        now = now + 59
        emit(inBackoff())
        now = now + 1
        emit(inBackoff())
        hosts["http://main.example:8080"] = "up"
        get("/api/x")
        emit(inBackoff())
        emit(net_failures)
    """)
    assert out == [True, True, False, False, 0]


def test_reset_clears_the_window_for_interactive_actions():
    out = _run(r"""
        hosts["http://main.example:8080"] = "down"
        get("/api/x"); get("/api/x")
        emit(inBackoff())
        resetBackoff()
        emit(inBackoff())
        emit(net_failures)
    """)
    assert out == [True, False, 0]


def test_without_a_fallback_only_the_main_url_is_tried():
    out = _run(r"""
        hosts["http://main.example:8080"] = "down"
        get("/api/x")
        emit(calls)
    """)
    assert out == [["http://main.example:8080"]]


def test_fallback_answers_when_main_is_dead_and_is_remembered():
    out = _run(r"""
        settings.tomesync_fallback_url = "http://192.168.1.10:8080"
        hosts["http://main.example:8080"] = "down"
        hosts["http://192.168.1.10:8080"] = "up"
        local ok, code = get("/api/x")
        emit(code)
        emit(calls)
        emit(settings.tomesync_active_url)
        emit(inBackoff())
        -- Next request goes straight to the address that answered.
        calls = {}
        get("/api/y")
        emit(calls)
        emit(serverURL())
    """)
    assert out == [
        200,
        ["http://main.example:8080", "http://192.168.1.10:8080"],
        "http://192.168.1.10:8080",
        False,
        ["http://192.168.1.10:8080"],
        "http://192.168.1.10:8080",
    ]


def test_network_switch_fails_over_back_to_main_once():
    # Left home: the LAN fallback stops answering, the main URL works again.
    out = _run(r"""
        settings.tomesync_fallback_url = "http://192.168.1.10:8080"
        settings.tomesync_active_url = "http://192.168.1.10:8080"
        hosts["http://main.example:8080"] = "up"
        get("/api/x")
        emit(calls)
        emit(settings.tomesync_active_url)
        calls = {}
        get("/api/y")
        emit(calls)
    """)
    assert out == [
        ["http://192.168.1.10:8080", "http://main.example:8080"],
        None,
        ["http://main.example:8080"],
    ]


def test_both_addresses_dead_is_one_miss_not_two():
    out = _run(r"""
        settings.tomesync_fallback_url = "http://192.168.1.10:8080"
        get("/api/x")
        emit(#calls)
        emit(net_failures)
        emit(backoff_until - now)
    """)
    assert out == [2, 1, 60]


def test_fallback_equal_to_main_or_empty_is_ignored():
    out = _run(r"""
        settings.tomesync_fallback_url = "http://main.example:8080"
        emit(#serverURLs())
        settings.tomesync_fallback_url = ""
        emit(#serverURLs())
        -- A remembered address that is no longer configured is ignored too.
        settings.tomesync_fallback_url = nil
        settings.tomesync_active_url = "http://old.example"
        emit(serverURL())
    """)
    assert out == [1, 1, "http://main.example:8080"]


def test_http_error_is_an_answer_not_a_miss():
    # A 500 from the main URL must not fail over or start the back-off.
    out = _run(r"""
        settings.tomesync_fallback_url = "http://192.168.1.10:8080"
        http.request = function(req)
            calls[#calls + 1] = req.url:match("^(https?://[^/]+)")
            return 1, 500
        end
        local ok, code = get("/api/x")
        emit(code)
        emit(#calls)
        emit(inBackoff())
    """)
    assert out == [500, 1, False]


def test_each_attempt_gets_a_fresh_sink():
    # A broken-off attempt on one address must not leave data in front of
    # the next attempt's body.
    out = _run(r"""
        settings.tomesync_fallback_url = "http://192.168.1.10:8080"
        hosts["http://192.168.1.10:8080"] = "up"
        http.request = function(req)
            local base = req.url:match("^(https?://[^/]+)")
            req.sink(base == "http://main.example:8080" and "partial" or "full")
            if base == "http://main.example:8080" then return nil, "timeout" end
            return 1, 200
        end
        local chunks
        serverRequest({
            method = "GET", path = "/api/x", headers = {},
            sink = function() chunks = {} return ltn12.sink.table(chunks) end,
            block = 5, total = 45,
        })
        emit(table.concat(chunks))
    """)
    assert out == ["full"]
