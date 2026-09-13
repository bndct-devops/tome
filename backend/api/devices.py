"""Connected devices: native clients signed in as a user (see ClientDevice)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.client_device import ClientDevice
from backend.models.user import User
from backend.services.audit import audit

router = APIRouter(prefix="/auth/devices", tags=["auth"])


class DeviceListItem(BaseModel):
    id: int
    name: str
    platform: Optional[str]
    app_version: Optional[str]
    created_at: datetime
    last_seen_at: Optional[datetime]
    revoked_at: Optional[datetime]
    user_id: int
    username: str

    @field_serializer("created_at", "last_seen_at", "revoked_at")
    def _utc_z(self, dt: Optional[datetime]) -> Optional[str]:
        # Stored naive-UTC; emit an explicit Z so browsers do not read it as local time.
        return dt.isoformat() + "Z" if dt else None


@router.get("", response_model=list[DeviceListItem])
def list_devices(
    all: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The current user's devices. Admin-only ?all=true returns everyone's."""
    if all and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    query = db.query(ClientDevice).options(joinedload(ClientDevice.user))
    if not all:
        query = query.filter(ClientDevice.user_id == current_user.id)
    rows = query.order_by(ClientDevice.created_at.desc()).all()
    return [
        DeviceListItem(
            id=d.id, name=d.name, platform=d.platform, app_version=d.app_version,
            created_at=d.created_at, last_seen_at=d.last_seen_at, revoked_at=d.revoked_at,
            user_id=d.user_id, username=d.user.username,
        )
        for d in rows
    ]


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(
    device_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign a device out for good. Owner or admin only."""
    device = db.query(ClientDevice).filter(ClientDevice.id == device_id).first()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    if device.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if device.revoked_at is None:
        device.revoked_at = datetime.utcnow()
        db.commit()
        ip = request.client.host if request.client else None
        audit(db, "device.revoke", user_id=current_user.id, username=current_user.username,
              resource_type="device", resource_id=device.id, resource_title=device.name, ip=ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
