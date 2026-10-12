"""Plugin build 32 hygiene batch — generated-impl contract tests.

Three features: the dedicated state file (data tables out of G_reader_settings),
pull-conflict strategy settings, and the device-side half of the clock-offset
guard. Server-side clock-offset behavior is covered in
test_tomesync_clock_offset.py.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.api.tome_sync import _main_impl_lua


def _impl() -> str:
    return _main_impl_lua("https://tome.example.org", "tk_testkey", "tester")


def _body(lua: str, func: str) -> str:
    # Line-anchored: "function TomeSync:init" also appears inside validateImpl's
    # string literals, so a bare find() would land there.
    start = lua.find(f"\nfunction TomeSync:{func}")
    assert start != -1, f"missing function {func}"
    return lua[start:lua.find("\nfunction ", start + 1)]


def test_impl_compiles_under_luajit():
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    with tempfile.NamedTemporaryFile(suffix=".lua", delete=False, mode="w") as f:
        f.write(_impl())
        path = f.name
    try:
        r = subprocess.run([luajit, "-bl", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        Path(path).unlink(missing_ok=True)


# ── dedicated state file ─────────────────────────────────────────────────────

DATA_KEYS = [
    "tomesync_book_map", "tomesync_pending_sessions", "tomesync_adopt_pending",
    "tomesync_repair_map", "tomesync_annot_baseline", "tomesync_rating_baseline",
    "tomesync_pending_ratings",
]


def test_data_tables_live_in_state_file_not_global():
    lua = _impl()
    assert 'LuaSettings:open(DataStorage:getSettingsDir() .. "/tomesync_state.lua")' in lua
    for key in DATA_KEYS:
        assert f'G_reader_settings:readSetting("{key}")' not in _body(lua, "init"), key
        assert f'self.state:readSetting("{key}")' in _body(lua, "init"), key
        # No save site may write a data table back to the global settings file.
        assert f'G_reader_settings:saveSetting("{key}"' not in lua, key


def test_update_state_stays_in_global_settings():
    # The frozen shim reads tomesync_update from G_reader_settings and is never
    # replaced by self-update — the impl must keep writing it there.
    lua = _impl()
    assert 'G_reader_settings:readSetting("tomesync_update")' in lua
    assert 'G_reader_settings:saveSetting("tomesync_update"' in lua


def test_migration_is_crash_safe_ordered():
    # New file must be flushed BEFORE the old keys are deleted, and the marker
    # branch re-deletes leftovers from a crash between the two flushes.
    body = _body(_impl(), "_migrateState")
    assert 'readSetting("migrated_from_global")' in body
    flush_new = body.find("self.state:flush()")
    del_old = body.rfind("G_reader_settings:delSetting(k)")
    assert flush_new != -1 and del_old != -1 and flush_new < del_old


def test_save_state_writes_through():
    body = _body(_impl(), "_saveState")
    assert "self.state:saveSetting(key, value)" in body
    assert "self.state:flush()" in body


def test_prune_never_touches_pending_queues():
    body = _body(_impl(), "_pruneState")
    assert "pending_sessions" not in body.replace(
        "Queues (pending_sessions/pending_ratings) are never pruned", "")
    assert "tomesync_annot_baseline" in body
    assert "tomesync_repair_map" in body


# ── pull-conflict strategy ────────────────────────────────────────────────────

def test_pull_modes_cover_both_directions_with_compatible_defaults():
    # Build 49: the pull runs in _pullPositionAtOpen, scheduled by _initSession.
    body = _body(_impl(), "_pullPositionAtOpen")
    assert 'readSetting("tomesync_pull_forward") or "silent"' in body
    assert 'readSetting("tomesync_pull_backward") or "never"' in body
    # Backward pull exists and is bounded away from 0%.
    assert "server_pct < (local_pct - 0.01)" in body


def test_prompt_is_deferred_off_the_open_path():
    # A ConfirmBox at open time would eat the Profiles auto-exec dispatch
    # exactly like the InfoMessage layout-reset bug — it must be scheduled.
    body = _body(_impl(), "_pullPositionAtOpen")
    prompt_at = body.find('mode == "prompt"')
    sched_at = body.find("UIManager:scheduleIn", prompt_at)
    confirm_at = body.find("ConfirmBox:new", prompt_at)
    assert prompt_at != -1 and sched_at != -1 and confirm_at != -1
    assert sched_at < confirm_at, "ConfirmBox must be inside the deferred callback"


def test_pull_settings_menu_items_exist():
    lua = _impl()
    assert "Server position is ahead" in lua
    assert "Server position is behind" in lua


# ── clock-offset guard, device half ──────────────────────────────────────────

def test_sync_request_carries_device_time():
    body = _body(_impl(), "_syncAnnotations")
    assert "device_time = os.date" in body


def test_future_stamps_are_scrubbed_before_diffing():
    body = _body(_impl(), "_syncAnnotations")
    assert "L.mtime > now" in body
    assert "baseline[anchor] = now" in body


def test_incoming_stamps_are_clamped_to_device_clock():
    body = _body(_impl(), "_applyServerState")
    assert "clampStamp" in body
    assert "device_now" in body
