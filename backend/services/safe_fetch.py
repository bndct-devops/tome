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


# Carrier-grade NAT (RFC 6598). Python's is_private leaves it out, but it is
# where Tailscale and many ISP-side networks put their hosts.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return False
    # An IPv4-mapped IPv6 address (::ffff:10.0.0.1) is judged as the IPv4 one.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    if ip.version == 4 and ip in _CGNAT:
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
    # Keep the resolver's order (it puts the preferred family first).
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips:
        raise UnsafeURLError(f"No addresses resolved for {host!r}")
    for ip in ips:
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"{host!r} resolves to non-public address {ip}")

    return host, ips[0]


def validate_public_url(url: str) -> tuple[str, str]:
    """Public name for the check above: (host, one checked address), or
    UnsafeURLError when the scheme is not http(s) or any address the host
    resolves to is not public."""
    return _validate_url(url)


def _pinned_request(client: httpx.AsyncClient, url: str, host: str, ip: str) -> httpx.Request:
    """A GET for ``url`` that connects to ``ip``, the address the check just
    approved, instead of resolving the name a second time (which a DNS
    answer that changes between the two lookups could point inside). The
    Host header keeps the name, and for https so does TLS: SNI and the
    certificate check use ``sni_hostname``."""
    original = httpx.URL(url)
    pinned = original.copy_with(host=ip)
    host_header = original.netloc.decode("ascii")
    extensions = {"sni_hostname": host} if original.scheme == "https" else {}
    return client.build_request("GET", pinned, headers={"Host": host_header}, extensions=extensions)


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
            host, ip = _validate_url(current)
            resp = await client.send(_pinned_request(client, current, host, ip))
            if resp.is_redirect:
                if hop == max_redirects:
                    raise UnsafeURLError("Too many redirects")
                next_url = resp.headers.get("Location")
                if not next_url:
                    raise UnsafeURLError("Redirect with no Location header")
                # Relative to the URL as the user gave it, not the pinned one.
                current = str(httpx.URL(current).join(next_url))
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
