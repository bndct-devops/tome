"""Request body size cap (TOME_MAX_UPLOAD_MB).

The multipart parser reads a whole upload into a temp file before a route
runs, so a size check in the route alone would still let any size reach the
disk. This plain-ASGI middleware refuses a declared Content-Length over the
cap before reading anything, and stops a body without one (chunked) as soon
as it passes the cap; either way the client gets 413. Book upload routes
also stop their own copy at the cap (see api/books.py:_save_upload).
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.config import settings

# Admin backup restores can be larger than any book and are admin-only.
EXEMPT_PATHS = ("/api/admin/backup/restore",)


class _TooLarge(Exception):
    pass


def _limit_bytes() -> int:
    return max(settings.max_upload_mb, 0) * 1024 * 1024


def _too_large_body() -> bytes:
    return json.dumps(
        {"detail": f"File is larger than the upload limit ({settings.max_upload_mb} MB)"}
    ).encode()


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _limit_bytes()
        if (
            scope["type"] != "http"
            or not limit
            or scope.get("method") in ("GET", "HEAD", "OPTIONS")
            or scope.get("path", "").rstrip("/").endswith(EXEMPT_PATHS)
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await _send_413(send)
                    return
                break

        received = 0
        exceeded = False
        started = False

        async def receive_wrapper() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise _TooLarge()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge()
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if exceeded:
                return  # the 413 replaces whatever the route makes of a cut body
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _TooLarge:
            pass
        if exceeded and not started:
            await _send_413(send)


async def _send_413(send: Send) -> None:
    body = _too_large_body()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
