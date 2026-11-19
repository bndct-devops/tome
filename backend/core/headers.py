"""Security headers for the web UI.

The Content-Security-Policy goes on the HTML pages the server serves for the
app. Its job: script inside a book never runs. The reader shows each EPUB
section in an iframe loaded from a blob: URL, and a blob: document inherits
the policy of the page that made it, so with ``script-src 'self'`` (no
'unsafe-inline', no blob:) a ``<script>`` in a chapter cannot reach the login
token in localStorage. The app's own two inline boot scripts in index.html are
allowed by their sha256 hash, computed from the file actually served (so the
new and the classic bundle both work, and a rebuilt index.html needs no code
change).

TOME_CONTENT_SECURITY_POLICY=off drops the header (an escape hatch for an
operator whose proxy or setup trips over it).
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

_INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_SRC_ATTR = re.compile(r"\bsrc\s*=", re.IGNORECASE)

_OFF_VALUES = {"off", "false", "0", "no", "disabled"}


def csp_enabled(value: str | None) -> bool:
    return (value or "on").strip().lower() not in _OFF_VALUES


def inline_script_hashes(html: str) -> list[str]:
    """CSP source expressions for every inline <script> in ``html``."""
    out: list[str] = []
    for attrs, body in _INLINE_SCRIPT.findall(html):
        if _SRC_ATTR.search(attrs):
            continue
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        source = f"'sha256-{digest}'"
        if source not in out:
            out.append(source)
    return out


def build_csp(script_hashes: list[str] | tuple[str, ...] = ()) -> str:
    script_src = " ".join(["'self'", *script_hashes])
    directives = [
        "default-src 'self'",
        f"script-src {script_src}",
        # Books bring their own stylesheets and fonts, which the reader hands
        # to the section iframe as blob: URLs.
        "style-src 'self' 'unsafe-inline' blob:",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "connect-src 'self' blob: data:",
        "frame-src 'self' blob:",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'self'",
    ]
    return "; ".join(directives)


_cache: dict[Path, tuple[float, str]] = {}


def csp_for_file(path: Path) -> str:
    """The policy for one HTML file, cached until the file changes."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return build_csp()
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return build_csp()
    policy = build_csp(inline_script_hashes(html))
    _cache[path] = (mtime, policy)
    return policy


# ── Headers on every response ────────────────────────────────────────────────

BASIC_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
)


class SecurityHeadersMiddleware:
    """Adds nosniff, a same-origin referrer policy and SAMEORIGIN framing to
    every HTTP response (a header the route already set is left alone).
    Plain ASGI, like compression.py, so streamed and file responses pass
    through untouched."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for name, value in BASIC_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
