"""Idle-capped session accounting in the TomeSync plugin (issue #150).

A device whose cover fails to suspend it books wall-clock time: the plugin
used ``duration = os.time() - session_start`` and only ended sessions on
suspend/close, so falling asleep over an open book logged an 8-hour session
(58h total on a 10h book, per the report). Build 37 accumulates active time
per page turn instead, crediting each gap at most the idle cap (default
10 min, configurable, 0 = off), and ends the session at the last activity
plus the final credit rather than at the moment the device finally slept.

Two layers of tests: regex tripwires on the generated Lua that always run
(same style as test_tomesync_initsession.py), and an executable harness that
extracts the real accounting functions and runs them under LuaJIT — the
engine KOReader itself uses — when one is installed.
"""
import re
import shutil
import subprocess
import textwrap

import pytest

from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD, _main_impl_lua


def _impl() -> str:
    return _main_impl_lua("http://localhost:8080", "tk_test", "tester")


def _block(lua: str, header: str) -> str:
    match = re.search(re.escape(header) + r".*?\nend\n", lua, re.S)
    assert match, f"{header} not found in generated impl"
    return match.group(0)


# ── Tripwires (always run) ────────────────────────────────────────────────────

def test_build_carries_the_idle_cap():
    assert TOMESYNC_PLUGIN_BUILD >= 37


def test_wall_clock_duration_formula_is_gone():
    # The old formula must not survive anywhere: every session-end path goes
    # through _sessionTotals or the runaway-session bug is back.
    lua = _impl()
    assert "os.time() - self.session_start" not in lua


def test_page_turns_credit_activity():
    body = _block(_impl(), "function TomeSync:onPageUpdate(pageno)")
    assert "self:_creditActivity(os.time())" in body


def test_both_session_end_paths_use_capped_totals():
    lua = _impl()
    for header in ("function TomeSync:onSuspend()",
                   "function TomeSync:onCloseDocument()"):
        body = _block(lua, header)
        assert "self:_sessionTotals(os.time())" in body, header
        # ended_at must be the honest end (last activity + credit), not the
        # moment the device finally suspended hours later.
        assert re.search(r'ended_at\s*=\s*os\.date\("!%Y-%m-%dT%H:%M:%SZ", session_end\)', body), header


def test_session_state_resets_everywhere():
    lua = _impl()
    for header in ("function TomeSync:_initSession()",
                   "function TomeSync:onResume()",
                   "function TomeSync:onCloseDocument()"):
        body = _block(lua, header)
        assert "self.active_seconds = 0" in body, header


def test_settings_menu_offers_the_cap():
    lua = _impl()
    assert "Idle time cap" in lua
    assert "tomesync_idle_cap_minutes" in lua


# ── Executable harness (LuaJIT — the engine KOReader runs on) ─────────────────

LUAJIT = shutil.which("luajit")


def _extract(lua: str, pattern: str) -> str:
    match = re.search(pattern, lua, re.S)
    assert match, f"pattern not found in impl: {pattern}"
    return match.group(0)


def _harness() -> str:
    """The real accounting code lifted verbatim from the generated impl,
    wrapped with a G_reader_settings stub and scenario assertions."""
    lua = _impl()
    cap_default = _extract(lua, r"local IDLE_CAP_DEFAULT_MINUTES = \d+")
    idle_cap = _extract(lua, r"local function idleCapSeconds\(\).*?\nend\n")
    credit = _extract(lua, r"function TomeSync:_creditActivity\(now\).*?\nend\n")
    totals = _extract(lua, r"function TomeSync:_sessionTotals\(now\).*?\nend\n")
    scenarios = textwrap.dedent("""
        local function newSession(t0)
            local s = setmetatable({}, {__index = TomeSync})
            s.session_start  = t0
            s.page_count     = 0
            s.active_seconds = 0
            s.last_activity  = t0
            return s
        end

        -- Normal reading: a page a minute for 30 minutes, end 60s after the
        -- last turn. Nothing hits the cap; duration is the full wall span.
        local t0 = 1000000
        local s = newSession(t0)
        for i = 1, 30 do s:_creditActivity(t0 + i * 60) end
        local dur, ended = s:_sessionTotals(t0 + 30 * 60 + 60)
        assert(dur == 30 * 60 + 60, "normal reading: " .. dur)
        assert(ended == t0 + 30 * 60 + 60, "normal reading end: " .. ended)

        -- The reporter's case: 15 minutes of reading, reader falls asleep,
        -- device suspends 8 hours later. Books ~15m + one cap of tail, and
        -- the session ends near the last page turn, not at 6 AM.
        s = newSession(t0)
        for i = 1, 15 do s:_creditActivity(t0 + i * 60) end
        local last_turn = t0 + 15 * 60
        dur, ended = s:_sessionTotals(last_turn + 8 * 3600)
        assert(dur == 15 * 60 + 600, "fell asleep: " .. dur)
        assert(ended == last_turn + 600, "fell asleep end: " .. ended)

        -- Nap in the middle: 10 minutes read, 3 idle hours, 10 more minutes,
        -- end a minute later. Both reading stretches count; the nap costs
        -- one cap.
        s = newSession(t0)
        for i = 1, 10 do s:_creditActivity(t0 + i * 60) end
        local resume = t0 + 10 * 60 + 3 * 3600
        s:_creditActivity(resume)  -- first turn after waking: 3h gap, capped
        for i = 1, 10 do s:_creditActivity(resume + i * 60) end
        dur, ended = s:_sessionTotals(resume + 10 * 60 + 60)
        assert(dur == 10 * 60 + 600 + 10 * 60 + 60, "mid-session nap: " .. dur)

        -- Cap off (0): exact wall-clock behaviour of builds <= 36.
        G_reader_settings.vals.tomesync_idle_cap_minutes = 0
        s = newSession(t0)
        for i = 1, 15 do s:_creditActivity(t0 + i * 60) end
        dur, ended = s:_sessionTotals(t0 + 15 * 60 + 8 * 3600)
        assert(dur == 15 * 60 + 8 * 3600, "cap off: " .. dur)
        assert(ended == t0 + 15 * 60 + 8 * 3600, "cap off end: " .. ended)

        -- Custom 5-minute cap.
        G_reader_settings.vals.tomesync_idle_cap_minutes = 5
        s = newSession(t0)
        s:_creditActivity(t0 + 3600)
        dur = s:_sessionTotals(t0 + 3600)
        assert(dur == 300, "5m cap: " .. dur)
        G_reader_settings.vals.tomesync_idle_cap_minutes = nil

        -- Clock skew: a backwards jump must never go negative or crash.
        s = newSession(t0)
        s:_creditActivity(t0 - 500)
        dur = s:_sessionTotals(t0)
        assert(dur >= 0, "clock skew: " .. dur)

        print("HARNESS-OK")
    """)
    return "\n".join([
        "G_reader_settings = { vals = {},",
        "  readSetting = function(self, k) return self.vals[k] end }",
        cap_default,
        idle_cap,
        "TomeSync = {}",
        credit,
        totals,
        scenarios,
    ])


@pytest.mark.skipif(LUAJIT is None, reason="luajit not installed")
def test_accounting_scenarios_under_luajit(tmp_path):
    script = tmp_path / "harness.lua"
    script.write_text(_harness())
    proc = subprocess.run([LUAJIT, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "HARNESS-OK" in proc.stdout


@pytest.mark.skipif(LUAJIT is None, reason="luajit not installed")
def test_generated_impl_parses_under_luajit(tmp_path):
    script = tmp_path / "main_impl.lua"
    script.write_text(_impl())
    proc = subprocess.run(
        [LUAJIT, "-e", f"assert(loadfile('{script}'))"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
