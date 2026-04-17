"""SSRF-hardened HTTP fetch for user-supplied URLs (covers, etc.).

Usage:
    from backend.services.safe_fetch import fetch_safe_image, UnsafeURLError
    content = await fetch_safe_image(url)
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20
ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL is rejected as unsafe (bad scheme, private IP, etc.)."""


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return True


def _validate_url(url: str) -> tuple[str, str]:
    """Parse url, verify scheme, resolve host, ensure all A/AAAA records are public.

    Returns (host, resolved_ip) on success. Raises UnsafeURLError otherwise.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL missing host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS lookup failed for {host!r}: {e}")
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError(f"No addresses resolved for {host!r}")
    for ip in ips:
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"{host!r} resolves to non-public address {ip}")

    return host, next(iter(ips))


async def fetch_safe_image(
    url: str,
    *,
    max_bytes: int = MAX_COVER_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    timeout: float = TIMEOUT_SECONDS,
) -> bytes:
    """Fetch url and return body bytes. Raises UnsafeURLError or httpx.HTTPError."""
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        headers={"User-Agent": "Tome/1.0"},
    ) as client:
        for hop in range(max_redirects + 1):
            _validate_url(current)
            resp = await client.get(current)
            if resp.is_redirect:
                if hop == max_redirects:
                    raise UnsafeURLError("Too many redirects")
                next_url = resp.headers.get("Location")
                if not next_url:
                    raise UnsafeURLError("Redirect with no Location header")
                current = str(resp.next_request.url) if resp.next_request else next_url
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                raise UnsafeURLError(f"Expected image content-type, got {ctype!r}")
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise UnsafeURLError(f"Cover too large: {content_length} bytes")
            body = resp.content
            if len(body) > max_bytes:
                raise UnsafeURLError(f"Cover too large: {len(body)} bytes")
            return body
    raise UnsafeURLError("Exceeded redirect loop without returning")
