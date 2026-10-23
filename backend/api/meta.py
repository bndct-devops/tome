"""App metadata: What's-New changelog excerpts and the update check.

Both are deliberately small and self-contained:
- /meta/whats-new parses CHANGELOG.md for the section matching the running
  version (falling back to [Unreleased] on dev builds) so the frontend can show
  a one-time "what changed" panel after an upgrade.
- /meta/update-check reports the result of the daily background check
  (backend/services/update_check.py) against update_check_url, which sends
  only the version number. Admin-only and read-only: the endpoint itself
  never calls out. TOME_UPDATE_CHECK=false turns the check off.
"""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from backend import __version__
from backend.core.config import settings
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services.update_check import GITHUB_RELEASES_URL, cached_result

router = APIRouter(prefix="/meta", tags=["meta"])

_CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def parse_changelog_section(text: str, version: str,
                            unreleased_fallback: bool = False) -> list[dict]:
    """Entries of the ``## [version]`` section. Each ``- **Title.** body``
    bullet becomes {kind, title, body}; continuation lines fold into the body.

    ``unreleased_fallback`` is opt-in and NOT used by the endpoint: the panel
    must only ever show notes for the version actually running — a release cut
    without its changelog section shows nothing, never [Unreleased] content."""
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
    if not section and unreleased_fallback and version != "Unreleased":
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


@router.get("/features")
def features(current_user: User = Depends(get_current_user)) -> dict:
    """UI feature flags the frontend cannot infer from other endpoints."""
    return {"native_app": settings.native_app, "web_session_idle_days": settings.web_session_idle_days}


@router.get("/update-check")
def update_check(current_user: User = Depends(get_current_user)) -> dict:
    from backend.core.permissions import is_admin

    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    if not settings.update_check:
        return {"enabled": False, "current": __version__,
                "latest": None, "update_available": False, "url": GITHUB_RELEASES_URL}
    return cached_result()
