"""Quick Connect — sign in on a new device using a short code."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import create_access_token, get_current_user
from backend.models.quick_connect import QuickConnectCode, generate_code
from backend.services.audit import audit

router = APIRouter(prefix="/auth/quick-connect", tags=["auth"])

CODE_TTL_MINUTES = 5


class InitiateResponse(BaseModel):
    code: str
    expires_at: datetime


class AuthorizeRequest(BaseModel):
    code: str


@router.post("/initiate", response_model=InitiateResponse)
def initiate(db: Session = Depends(get_db)):
    """Generate a new Quick Connect code. No authentication required."""
    # Clean up expired codes
    db.query(QuickConnectCode).filter(QuickConnectCode.expires_at < datetime.utcnow()).delete()
    db.commit()

    now = datetime.utcnow()
    expires = now + timedelta(minutes=CODE_TTL_MINUTES)
    code = generate_code()
    # Ensure uniqueness (extremely unlikely collision but be safe)
    while db.query(QuickConnectCode).filter(QuickConnectCode.code == code).first():
        code = generate_code()

    entry = QuickConnectCode(code=code, created_at=now, expires_at=expires)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return InitiateResponse(code=entry.code, expires_at=entry.expires_at)


@router.post("/authorize", status_code=200)
def authorize(
    body: AuthorizeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Link a pending code to the currently authenticated user."""
    entry = db.query(QuickConnectCode).filter(QuickConnectCode.code == body.code.upper().strip()).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code not found")
    if entry.expires_at < datetime.utcnow():
        db.delete(entry)
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Code has expired")
    if entry.authorized_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already authorized")

    entry.user_id = current_user.id
    entry.authorized_at = datetime.utcnow()
    db.commit()

    ip = request.client.host if request.client else None
    audit(db, "auth.quick_connect_authorized",
          user_id=current_user.id, username=current_user.username, ip=ip,
          details={"code": body.code.upper().strip()})

    return {"status": "authorized"}


@router.get("/poll/{code}")
def poll(code: str, request: Request, db: Session = Depends(get_db)):
    """Poll for code authorization status. Returns JWT once authorized."""
    entry = db.query(QuickConnectCode).filter(QuickConnectCode.code == code.upper().strip()).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Code not found or expired")
    if entry.expires_at < datetime.utcnow():
        db.delete(entry)
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Code has expired")
    if entry.authorized_at is None or entry.user_id is None:
        return {"status": "pending"}

    # Authorized — issue a JWT and consume the code
    token = create_access_token(entry.user_id)
    user_id = entry.user_id

    db.delete(entry)
    db.commit()

    ip = request.client.host if request.client else None
    audit(db, "auth.quick_connect_login", user_id=user_id, ip=ip)

    return {"status": "authorized", "access_token": token, "token_type": "bearer"}
