"""Gzip for text responses only.

Starlette's GZipMiddleware compresses every response over its size floor,
which here would include book downloads (hundreds of MB, already compressed,
and their Content-Length would be dropped), covers and page images. This
variant compresses only text types: the API's JSON, the app's JS/CSS/HTML and
OPDS XML. Clients that do not send ``Accept-Encoding: gzip`` (KOReader's
luasocket among them) get the plain response, as before.
"""
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder, IdentityResponder
from starlette.types import Message, Receive, Scope, Send

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


class _TextGZipResponder(GZipResponder):
    async def send_with_compression(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            await super().send_with_compression(message)
            ct = Headers(raw=message["headers"]).get("content-type", "")
            self.content_type_is_excluded = not compressible(ct)
            return
        await super().send_with_compression(message)


class TextGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover
            await self.app(scope, receive, send)
            return
        if "gzip" in Headers(scope=scope).get("Accept-Encoding", ""):
            responder = _TextGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
        else:
            responder = IdentityResponder(self.app, self.minimum_size)
        await responder(scope, receive, send)
