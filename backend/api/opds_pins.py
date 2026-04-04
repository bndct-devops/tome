"""OPDS PIN management endpoints. JWT-authenticated."""
from datetime import datetime
from typing import List, Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.opds_pin import OpdsPin
from backend.models.user import User

router = APIRouter(prefix="/opds-pins", tags=["opds-pins"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class OpdsPinOut(BaseModel):
    id: int
    label: str
    pin_preview: str
    created_at: datetime
    last_used_at: Optional[datetime]

    model_config = {"from_attributes": True}


class OpdsPinCreate(BaseModel):
    label: str = "KOReader"


class OpdsPinCreated(BaseModel):
    id: int
    pin: str
    label: str
    pin_preview: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[OpdsPinOut])
def list_opds_pins(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List the current user's OPDS PINs."""
    return db.query(OpdsPin).filter(OpdsPin.user_id == user.id).order_by(OpdsPin.created_at).all()


@router.post("", response_model=OpdsPinCreated, status_code=status.HTTP_201_CREATED)
def create_opds_pin(
    body: OpdsPinCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a new OPDS PIN. The plain PIN is returned once and never stored."""
    pin = OpdsPin.generate()
    hashed = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    preview = OpdsPin.make_preview(pin)

    entry = OpdsPin(
        user_id=user.id,
        hashed_pin=hashed,
        label=body.label,
        pin_preview=preview,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return OpdsPinCreated(id=entry.id, pin=pin, label=entry.label, pin_preview=preview)


@router.delete("/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_opds_pin(
    pin_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revoke (delete) an OPDS PIN. Users may only delete their own PINs."""
    entry = db.query(OpdsPin).filter(OpdsPin.id == pin_id).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PIN not found")
    if entry.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your PIN")
    db.delete(entry)
    db.commit()
