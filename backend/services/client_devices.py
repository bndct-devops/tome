"""Issue login tokens. Every login registers a ClientDevice: the Tome app names
itself, a web browser is named from its User-Agent."""
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.security import create_access_token
from backend.models.client_device import ClientDevice
from backend.schemas.auth import DeviceInfo

# Order matters: Edge and Opera carry "Chrome" too, Chrome carries "Safari".
_BROWSERS = (
    (r"Edg(e|A|iOS)?/", "Edge"),
    (r"OPR/|Opera", "Opera"),
    (r"Firefox/|FxiOS/", "Firefox"),
    (r"SamsungBrowser/", "Samsung Internet"),
    (r"Chrome/|CriOS/", "Chrome"),
    (r"Safari/", "Safari"),
)
# iPadOS 13+ Safari reports itself as a Mac, so it is listed as macOS.
_SYSTEMS = (
    (r"iPhone", "iOS"),
    (r"iPad", "iPadOS"),
    (r"Android", "Android"),
    (r"CrOS", "ChromeOS"),
    (r"Windows", "Windows"),
    (r"Mac OS X|Macintosh", "macOS"),
    (r"Linux", "Linux"),
)


def browser_label(user_agent: str | None) -> tuple[str, str | None]:
    """(browser, system) for the devices list, e.g. ("Safari", "macOS")."""
    ua = user_agent or ""
    name = next((label for pattern, label in _BROWSERS if re.search(pattern, ua)), "Browser")
    system = next((label for pattern, label in _SYSTEMS if re.search(pattern, ua)), None)
    return name, system


def issue_token(
    db: Session, user_id: int, device: DeviceInfo | None, user_agent: str | None = None,
) -> str:
    """A device-bound JWT. `device` is how the Tome app names itself; without
    it the login is a web browser, named from `user_agent`."""
    if device is not None and device.name.strip():
        name = device.name.strip()[:120]
        platform = (device.platform or "").strip()[:120] or None
        app_version = (device.app_version or "").strip()[:40] or None
        kind = "app"
    else:
        name, platform = browser_label(user_agent)
        app_version = None
        kind = "web"
    now = datetime.utcnow()
    row = ClientDevice(
        user_id=user_id,
        token_id=secrets.token_hex(16),
        name=name,
        platform=platform,
        app_version=app_version,
        kind=kind,
        created_at=now,
        last_seen_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return create_access_token(user_id, device_token_id=row.token_id)


def prune_idle_web(db: Session) -> int:
    """Delete browser sign-ins unused for `web_session_idle_days`, revoked
    ones included, so abandoned browsers do not pile up in the list. A live
    one is also caught on its next request (security.resolve_jwt). Commits."""
    days = settings.web_session_idle_days
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    seen = func.coalesce(ClientDevice.last_seen_at, ClientDevice.created_at)
    deleted = (
        db.query(ClientDevice)
        .filter(ClientDevice.kind == "web", seen < cutoff)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def revoke_all(db: Session, user_id: int, keep_id: int | None = None) -> int:
    """Revoke every active device of a user except `keep_id` (the one making
    the request). Returns how many were revoked. The caller commits."""
    query = db.query(ClientDevice).filter(
        ClientDevice.user_id == user_id, ClientDevice.revoked_at.is_(None),
    )
    if keep_id is not None:
        query = query.filter(ClientDevice.id != keep_id)
    return query.update({ClientDevice.revoked_at: datetime.utcnow()}, synchronize_session=False)
