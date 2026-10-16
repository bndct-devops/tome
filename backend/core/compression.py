"""Gzip for text responses only.

Starlette's GZipMiddleware compresses every response over its size floor,
which here would include book downloads (hundreds of MB, already compressed,
and their Content-Length would be dropped), covers and page images. This one
compresses only text types: the API's JSON, the app's JS/CSS/HTML and OPDS
XML. Clients that do not send ``Accept-Encoding: gzip`` (KOReader's luasocket
among them) get the plain response, as before.

It is a plain ASGI middleware on purpose: an earlier version subclassed
Starlette's GZip responder, whose handling of the start message changed
between Starlette releases (1.8 sends excluded responses' start itself), and
non-text responses lost their headers.
"""
import zlib

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_COMPRESSIBLE = (
    "application/json",
    "application/javascript",
    "application/xml",
    "application/atom+xml",
    "application/manifest+json",
    "image/svg+xml",
    "text/",
)


def compressible(content_type: str) -> bool:
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct.startswith(_COMPRESSIBLE) and ct != "text/event-stream"


class TextGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or "gzip" not in Headers(scope=scope).get("accept-encoding", ""):
            await self.app(scope, receive, send)
            return

        start: Message | None = None  # held until the first body decides
        passthrough = False
        compressor = None

        async def send_wrapper(message: Message) -> None:
            nonlocal start, passthrough, compressor
            kind = message["type"]
            if kind == "http.response.start":
                headers = Headers(raw=message["headers"])
                if (
                    "content-encoding" in headers
                    or message.get("status") == 206
                    or not compressible(headers.get("content-type", ""))
                ):
                    passthrough = True
                    await send(message)
                else:
                    start = message
                return
            if passthrough:
                await send(message)
                return
            if kind != "http.response.body":
                # anything else (e.g. pathsend): send the held start untouched first
                if start is not None:
                    await send(start)
                    start = None
                passthrough = True
                await send(message)
                return

            body = message.get("body", b"")
            more = message.get("more_body", False)
            if start is not None:
                held, start = start, None
                if not more and len(body) < self.minimum_size:
                    passthrough = True
                    await send(held)
                    await send(message)
                    return
                headers = MutableHeaders(raw=held["headers"])
                headers.add_vary_header("Accept-Encoding")
                headers["Content-Encoding"] = "gzip"
                compressor = zlib.compressobj(self.compresslevel, zlib.DEFLATED, 31)  # 31: gzip container
                data = compressor.compress(body) + (compressor.flush() if not more else compressor.flush(zlib.Z_SYNC_FLUSH))
                if more:
                    del headers["Content-Length"]
                else:
                    headers["Content-Length"] = str(len(data))
                await send(held)
                await send({"type": "http.response.body", "body": data, "more_body": more})
                return
            assert compressor is not None
            data = compressor.compress(body) + (compressor.flush() if not more else compressor.flush(zlib.Z_SYNC_FLUSH))
            await send({"type": "http.response.body", "body": data, "more_body": more})

        await self.app(scope, receive, send_wrapper)
