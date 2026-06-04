"""Public-origin resolution for URLs Tome hands to external clients.

The single source of truth for "what scheme://host is Tome actually reachable
at from the outside" — used both for the KOReader plugin's baked SERVER_URL and
the OIDC redirect URI. Both break in the same way behind a TLS-terminating
reverse proxy: the app server sees ``http`` on the wire, so a naive
``request.base_url`` produces an ``http://`` origin even though clients reach
Tome over ``https``. For the plugin that broke POST/PUT sync; for OIDC it makes
the redirect_uri mismatch the (https) URL registered at the IdP.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from starlette.requests import Request

from backend.core.config import settings


def public_base_url(request: Request, explicit: str | None = None) -> str:
    """Resolve Tome's public origin (no trailing slash).

    Priority:
      1. ``explicit`` — a caller-supplied override (e.g. the web UI passing
         ``?server_url=`` to dodge the Vite dev proxy).
      2. ``TOME_PUBLIC_URL`` — the authoritative configured public origin.
      3. the request origin, with the scheme taken from ``X-Forwarded-Proto``
         when a proxy sent it (the HTTPS-behind-a-proxy fix). When the header is
         absent (plain HTTP / LAN / localhost) the scheme is left untouched.
    """
    if explicit:
        return explicit.rstrip("/")
    if settings.public_url:
        return settings.public_url.rstrip("/")
    base = str(request.base_url).rstrip("/")
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        # May be a comma-separated chain (e.g. "https,http"); the client-facing
        # scheme is the first hop.
        proto = forwarded.split(",")[0].strip().lower()
        if proto in ("http", "https"):
            parts = urlsplit(base)
            base = urlunsplit((proto, parts.netloc, parts.path, parts.query, parts.fragment))
    return base.rstrip("/")


def is_secure_origin(origin: str) -> bool:
    """True if the origin is safe to emit as a redirect target / public URL.

    ``https`` is always fine; plain ``http`` is only acceptable for local dev
    (localhost / 127.0.0.1). Anything else means we'd be handing an ``http``
    URL to an external IdP — which won't match the registered https callback.
    """
    parts = urlsplit(origin)
    if parts.scheme == "https":
        return True
    host = (parts.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")
