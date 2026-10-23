"""Background update check: does a newer Tome release exist?

Once a day (first run 5-60 minutes after startup, then every 24 h +/- 2 h)
Tome asks ``settings.update_check_url`` for the latest release. The request
carries the running version number and nothing else:

    GET <update_check_url>?v=<version>
    User-Agent: Tome/<version>
    Accept: application/json
    -> {"version": "2.7.1", "url": "https://github.com/.../releases/tag/v2.7.1"}

If that fails, the GitHub releases API is tried once as a fallback. If both
fail the previous result is kept and the next try comes after 1 h, then 6 h,
then back on the daily cadence.

``/api/meta/update-check`` only reads the cached result; it never calls out.
TOME_UPDATE_CHECK=false keeps the loop from starting at all.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Awaitable, Callable

from backend import __version__
from backend.core.config import settings

log = logging.getLogger(__name__)

GITHUB_RELEASES_API = "https://api.github.com/repos/bndct-devops/tome/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/bndct-devops/tome/releases"

FIRST_DELAY_RANGE = (5 * 60.0, 60 * 60.0)
CADENCE_RANGE = (22 * 3600.0, 26 * 3600.0)
# Retry delays after consecutive failures; past the end, the daily cadence.
BACKOFF = (3600.0, 6 * 3600.0)

_cache_lock = threading.Lock()
# latest: newest release version, or None until one is known.
# url: release page for `latest`, or None (callers fall back to GITHUB_RELEASES_URL).
# at: time.time() of the last check that produced a version.
_cache: dict = {"latest": None, "url": None, "at": 0.0}


def _ver_tuple(v: str) -> tuple:
    parts = []
    for p in re.split(r"[.\-+]", v):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts or [0])


def cached_result() -> dict:
    """The /meta/update-check response for an enabled check, from the cache."""
    with _cache_lock:
        latest = _cache["latest"]
        url = _cache["url"]
    return {
        "enabled": True,
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest) and _ver_tuple(latest) > _ver_tuple(__version__),
        "url": url or GITHUB_RELEASES_URL,
    }


def reset_cache() -> None:
    with _cache_lock:
        _cache.update(latest=None, url=None, at=0.0)


def build_request() -> urllib.request.Request:
    """The one request sent to update_check_url: the version number, nothing else."""
    sep = "&" if "?" in settings.update_check_url else "?"
    url = f"{settings.update_check_url}{sep}{urllib.parse.urlencode({'v': __version__})}"
    return urllib.request.Request(
        url,
        headers={"User-Agent": f"Tome/{__version__}", "Accept": "application/json"},
    )


def _fetch_primary() -> tuple[str | None, str | None]:
    """(version, release url) from update_check_url. Raises on any failure:
    network error, non-2xx status, or a body that is not the expected JSON."""
    with urllib.request.urlopen(build_request(), timeout=10) as resp:
        status = getattr(resp, "status", 200)
        if not 200 <= status < 300:
            raise OSError(f"HTTP {status}")
        data = json.loads(resp.read().decode())
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    version = data.get("version")
    if version is not None and not isinstance(version, str):
        raise ValueError("version is not a string")
    url = data.get("url")
    if not (isinstance(url, str) and url.startswith("https://")):
        url = None
    return (version.lstrip("v") or None) if version else None, url


def _fetch_latest_release() -> str | None:
    """Latest release tag from GitHub ('1.9.0', no leading v), or None."""
    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tome-update-check"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        tag = json.loads(resp.read().decode()).get("tag_name") or ""
    return tag.lstrip("v") or None


def check_once() -> bool:
    """Run one check and update the cache. True if a lookup answered (primary
    or the GitHub fallback), False if both failed. A null version counts as an
    answer but leaves the previous result in place."""
    try:
        version, url = _fetch_primary()
    except Exception as exc:  # noqa: BLE001 — network failures are expected offline
        log.debug("update-check: %s failed: %s", settings.update_check_url, exc)
        try:
            version, url = _fetch_latest_release(), None
        except Exception as exc2:  # noqa: BLE001
            log.debug("update-check: GitHub fallback failed: %s", exc2)
            return False
    if version:
        with _cache_lock:
            _cache.update(latest=version, url=url, at=time.time())
    return True


class _Backoff:
    """Next delay after each check, plus the one-warning / one-recovery log."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.failures = 0
        self.rng = rng or random.Random()

    def cadence(self) -> float:
        return self.rng.uniform(*CADENCE_RANGE)

    def after(self, ok: bool) -> float:
        if ok:
            if self.failures:
                log.info("Update check: working again")
            self.failures = 0
            return self.cadence()
        self.failures += 1
        if self.failures == 1:
            log.warning("Update check: could not reach %s or GitHub; "
                        "retrying in 1 h, then 6 h, then daily",
                        settings.update_check_url)
        if self.failures <= len(BACKOFF):
            return BACKOFF[self.failures - 1]
        return self.cadence()


async def update_check_loop(
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> None:
    """Background task started from lifespan when settings.update_check is on."""
    backoff = _Backoff(rng)
    await sleep(backoff.rng.uniform(*FIRST_DELAY_RANGE))
    while True:
        try:
            ok = await asyncio.to_thread(check_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unhandled error in update-check loop")
            ok = False
        await sleep(backoff.after(ok))
