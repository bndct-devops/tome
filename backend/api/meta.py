"""App metadata: What's-New changelog excerpts and the update check.

Both are deliberately small and self-contained:
- /meta/whats-new parses CHANGELOG.md for the section matching the running
  version (falling back to [Unreleased] on dev builds) so the frontend can show
  a one-time "what changed" panel after an upgrade.
- /meta/update-check compares the running version against the latest GitHub
  release, cached in memory for a day. Admin-only, and TOME_UPDATE_CHECK=false
  turns it off entirely — nothing else phones home.
"""
import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from backend import __version__
from backend.core.config import settings
from backend.core.security import get_current_user
from backend.models.user import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/meta", tags=["meta"])

GITHUB_RELEASES_API = "https://api.github.com/repos/bndct-devops/tome/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/bndct-devops/tome/releases"

_CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def parse_changelog_section(text: str, version: str) -> list[dict]:
    """Entries of the ``## [version]`` section (or ``## [Unreleased]`` if that
    version has no section — dev builds). Each ``- **Title.** body`` bullet
    becomes {kind, title, body}; continuation lines fold into the body."""
    lines = text.splitlines()
    section: list[str] = []
    in_section = False
    for line in lines:
        m = re.match(r"^## \[([^\]]+)\]", line)
        if m:
            if in_section:
                break
            in_section = m.group(1) == version
            continue
        if in_section:
            section.append(line)
    if not section and version != "Unreleased":
        return parse_changelog_section(text, "Unreleased")

    entries: list[dict] = []
    kind = ""
    for line in section:
        km = re.match(r"^### (.+)$", line)
        if km:
            kind = km.group(1).strip()
            continue
        bm = re.match(r"^- (.+)$", line)
        if bm:
            entries.append({"kind": kind, "text": bm.group(1).strip()})
        elif line.startswith("  ") and entries:
            entries[-1]["text"] += " " + line.strip()
    # Split the conventional "**Title.** body" lead into title/body.
    for e in entries:
        raw = e.pop("text")
        tm = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", raw)
        if tm:
            e["title"], e["body"] = tm.group(1).strip(), tm.group(2).strip()
        else:
            e["title"], e["body"] = "", raw
    return entries


@router.get("/whats-new")
def whats_new(current_user: User = Depends(get_current_user)) -> dict:
    try:
        text = _CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return {"version": __version__, "entries": []}
    return {"version": __version__, "entries": parse_changelog_section(text, __version__)}


# ── Update check ──────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "result": None, "ttl": 0.0}
_TTL_OK = 24 * 3600.0     # successful lookups: once a day is plenty
_TTL_FAIL = 3600.0        # failures: retry hourly, don't hammer while offline


def _fetch_latest_release() -> str | None:
    """Latest release tag from GitHub ('1.9.0', no leading v), or None."""
    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tome-update-check"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        tag = json.loads(resp.read().decode()).get("tag_name") or ""
    return tag.lstrip("v") or None


def _ver_tuple(v: str) -> tuple:
    parts = []
    for p in re.split(r"[.\-+]", v):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts or [0])


@router.get("/update-check")
def update_check(current_user: User = Depends(get_current_user)) -> dict:
    from backend.core.permissions import is_admin

    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    if not settings.update_check:
        return {"enabled": False, "current": __version__,
                "latest": None, "update_available": False, "url": GITHUB_RELEASES_URL}

    now = time.time()
    with _cache_lock:
        if _cache["result"] is not None and now - _cache["at"] < _cache["ttl"]:
            return _cache["result"]

    latest: str | None = None
    try:
        latest = _fetch_latest_release()
        ttl = _TTL_OK
    except Exception as exc:  # noqa: BLE001 — network failures are expected offline
        log.info("update-check: GitHub lookup failed: %s", exc)
        ttl = _TTL_FAIL

    result = {
        "enabled": True,
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest) and _ver_tuple(latest) > _ver_tuple(__version__),
        "url": GITHUB_RELEASES_URL,
    }
    with _cache_lock:
        _cache.update(at=now, result=result, ttl=ttl)
    return result
