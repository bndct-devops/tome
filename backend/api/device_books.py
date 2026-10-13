"""GET/DELETE /api/device-books - which Tome books are on the user's KOReader devices.

The TomeSync plugin uploads each device's list (PUT /api/tome-sync/device-books,
API-key auth). These are the reading side for the web and the iOS app: the
"On <device>" pills come from `book_ids`, the per-device filter is the
`on_device` / `not_on_device` parameter of GET /api/books. Own devices only.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services import device_inventory

router = APIRouter(tags=["device-books"])


@router.get("/device-books")
def get_device_books(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The user's devices, newest sync first: name, synced_at (UTC), plugin
    build, and the ids of the visible books on each."""
    return device_inventory.payload(db, user)


@router.delete("/device-books/{device}", status_code=204)
def forget_device(
    device: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Forget a device's list (a device you no longer use). A device that is
    still in use sends its list again on its next sync."""
    if not device_inventory.forget(db, user, device):
        raise HTTPException(404, "No such device")
