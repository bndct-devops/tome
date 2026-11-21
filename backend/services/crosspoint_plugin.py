"""Tome's plugin for CrossPoint readers (experimental).

CrossPoint 2.0, and CrossInk, which shares its plugin system, load plugins
from a folder on the SD card. A plugin is not code. ``device.json`` tells the
firmware which requests to send; the firmware fills the ``{...}`` placeholders
in and sends them by itself. This one declares three things:

- ``auth``: how the reader is linked. The reader asks Tome for a code, shows
  it with a QR code, and keeps asking until the user has confirmed it on a
  signed-in device. It then stores the device key it is given in
  ``token.json``. Nothing is typed on the reader.
- ``events``: when a reading session ends, post it. ``"connect": true`` lets
  the reader join a saved Wi-Fi network when it goes to sleep to do so.
- ``browse``: a short list the reader shows when the plugin is opened (that
  it is linked, what Tome has received). The firmware only gives a plugin a
  screen, and with it the sign-in, when it has a list to show.

``config.json`` holds the one thing that differs per installation: the
server's address. The ZIP Tome builds has it filled in and holds nothing
about the account. The receiving end is ``backend/api/crosspoint.py``.

Every value in the session body is quoted: the firmware pastes values into
the template as text, and a missing one would otherwise leave invalid JSON.
The book's file name is left out for the same reason (a quote in it would
break the body) and because the document hash already identifies the book.
"""
from __future__ import annotations

import io
import json
import zipfile

NAME = "tome"
VERSION = "0.2.0"
TITLE = "Tome"
DESCRIPTION = "Sends your reading sessions to your Tome server when the reader goes to sleep."
API = "{cfg.server}/api/v1/crosspoint"

SESSION_FIELDS = (
    "document", "start_time", "end_time", "duration_seconds",
    "start_progress_bp", "end_progress_bp", "progress_scale",
)
_KEY = {"Authorization": "Bearer {token}"}
_JSON = {"Content-Type": "application/json"}


def session_body() -> str:
    """The request body template for a ``reader.session`` event."""
    fields = [("id", "{event.id}")] + [(f, "{event." + f + "}") for f in SESSION_FIELDS]
    return "{" + ",".join(f'"{key}":"{value}"' for key, value in fields) + "}"


def manifest() -> dict:
    return {
        "name": NAME,
        "title": TITLE,
        "description": DESCRIPTION,
        "author": "Tome",
        "version": VERSION,
        "files": ["manifest.json", "device.json"],
    }


def device() -> dict:
    return {
        "title": TITLE,
        "description": DESCRIPTION,
        "author": "Tome",
        "version": VERSION,
        "config": {"file": "config.json"},
        "token": {"file": "token.json", "path": "token"},
        "auth": {
            "type": "device_code",
            "request": {"method": "POST", "url": f"{API}/device/code", "headers": dict(_JSON), "body": "{}"},
            "poll": {
                "method": "POST",
                "url": f"{API}/device/token",
                "headers": dict(_JSON),
                "body": '{"device_code":"{device_code}"}',
            },
        },
        "browse": {
            "url": f"{API}/catalog",
            "headers": dict(_KEY),
            "items": "items",
            "fields": {"title": "title", "author": "subtitle", "id": "id"},
            "page_size": 8,
        },
        "events": {
            "reader.session": {
                "request": {
                    "method": "POST",
                    "url": f"{API}/sessions",
                    "headers": {**_JSON, **_KEY},
                    "body": session_body(),
                },
                "connect": True,
            },
        },
    }


def config(server: str) -> dict:
    return {"server": server.rstrip("/")}


def build_zip(server: str) -> bytes:
    """The plugin folder (``tome/``) as a ZIP, ready to unpack into
    ``/plugins`` on the reader's SD card."""
    files = {
        "manifest.json": manifest(),
        "device.json": device(),
        "config.json": config(server),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"{NAME}/{name}", json.dumps(content, indent=2) + "\n")
    return buf.getvalue()
