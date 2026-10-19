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
