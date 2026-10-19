"""Reader link (plugin build 51): the bake keyword and the Lua it renders.

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
                  '"/tome-sync/commands"', '"/tome-sync/commands/"'):
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
