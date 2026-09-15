"""Issue login tokens, registering a ClientDevice when the client identifies itself."""
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from backend.core.security import create_access_token
from backend.models.client_device import ClientDevice
from backend.schemas.auth import DeviceInfo


def issue_token(db: Session, user_id: int, device: DeviceInfo | None) -> str:
    """Plain JWT for web logins; a device-bound JWT when `device` is given."""
    if device is None or not device.name.strip():
        return create_access_token(user_id)
    row = ClientDevice(
        user_id=user_id,
        token_id=secrets.token_hex(16),
        name=device.name.strip()[:120],
        platform=(device.platform or "").strip()[:120] or None,
        app_version=(device.app_version or "").strip()[:40] or None,
        created_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return create_access_token(user_id, device_token_id=row.token_id)


def revoke_all(db: Session, user_id: int) -> int:
    """Revoke every active device of a user. Returns how many were revoked.
    The caller commits."""
    return (
        db.query(ClientDevice)
        .filter(ClientDevice.user_id == user_id, ClientDevice.revoked_at.is_(None))
        .update({ClientDevice.revoked_at: datetime.utcnow()}, synchronize_session=False)
    )
