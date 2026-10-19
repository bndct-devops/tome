#!/usr/bin/env python3
"""Write a ready-to-install tomesync.koplugin folder.

    tools/bake-tomesync.py <out_dir> --server-url URL --api-key KEY --username NAME
                           [--auto-deliver] [--fingerprint SHA256]
                           [--link-host https://host:port ...]

<out_dir> receives the four plugin files (_meta.lua, main.lua, main_impl.lua,
config.lua); copy it to KOReader's plugins/ as tomesync.koplugin. The Lua is
copied unchanged from backend/tomesync.koplugin; only config.lua is written,
in the same format Tome's zip download uses (backend/api/tome_sync.py
_config_lua: keep the two in step). Standard library only, so the system
python3 runs it (the iOS app build calls it).
"""
import argparse
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "backend" / "tomesync.koplugin"
PLUGIN_FILES = ("_meta.lua", "main.lua", "main_impl.lua")
HEX = "0123456789abcdefABCDEF:"


def lua_str(value: str) -> str:
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def config_lua(server_url: str, api_key: str, username: str,
               auto_deliver: bool = False, fingerprint: str = "",
               link_hosts=()) -> str:
    fingerprint = "".join(c for c in fingerprint if c in HEX)
    hosts = ("LINK_HOSTS = { " + ", ".join(lua_str(h) for h in link_hosts) + " }\n") if link_hosts else ""
    return (
        "-- TomeSync connection. Self-update never replaces this file.\n"
        f"SERVER_URL = {lua_str(server_url)}\n"
        f"API_KEY = {lua_str(api_key)}\n"
        f"USERNAME = {lua_str(username)}\n"
        f"AUTO_DELIVER = {'true' if auto_deliver else 'false'}\n"
        f"SERVER_FINGERPRINT = {lua_str(fingerprint)}\n"
        + hosts
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out_dir")
    p.add_argument("--server-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--auto-deliver", action="store_true")
    p.add_argument("--fingerprint", default="")
    p.add_argument("--link-host", action="append", default=[],
                   help="a LINK_HOSTS entry (repeatable): the reader link's phone address")
    a = p.parse_args(argv)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in PLUGIN_FILES:
        shutil.copyfile(PLUGIN_DIR / name, out / name)
    (out / "config.lua").write_text(
        config_lua(a.server_url, a.api_key, a.username, a.auto_deliver, a.fingerprint,
                   tuple(a.link_host)),
        encoding="utf-8",
    )
    print(f"wrote {out}: {', '.join(PLUGIN_FILES + ('config.lua',))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
