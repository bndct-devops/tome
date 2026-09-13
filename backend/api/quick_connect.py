"""Quick Connect — sign in on a new device using a short code.

The short code is only ever typed on the ALREADY-authenticated device
(authorize). The new device keeps a long random poll_token from initiate and
must present it to poll — so the 6-character code space cannot be brute-forced
into a login JWT.
"""
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    poll_token: str
    expires_at: datetime


class AuthorizeRequest(BaseModel):
    code: str


class PollRequest(BaseModel):
    code: str
    poll_token: str


class CodeStatusResponse(BaseModel):
    status: str  # "pending" | "authorized"
    expires_at: datetime


def _new_code(db: Session) -> QuickConnectCode:
    """Create and persist a fresh, unique code with the standard TTL."""
    db.query(QuickConnectCode).filter(QuickConnectCode.expires_at < datetime.utcnow()).delete()
    db.commit()

    now = datetime.utcnow()
    code = generate_code()
    while db.query(QuickConnectCode).filter(QuickConnectCode.code == code).first():
        code = generate_code()

    entry = QuickConnectCode(
        code=code,
        poll_token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _owned_live_code(db: Session, code: str, user_id: int) -> QuickConnectCode:
    """A code the current user issued or authorized, still within its TTL.
    Anything else is a plain 404 so the endpoint is not an oracle."""
    entry = db.query(QuickConnectCode).filter(QuickConnectCode.code == code.upper().strip()).first()
    if not entry or entry.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code not found")
    if entry.expires_at < datetime.utcnow():
        db.delete(entry)
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code not found")
    return entry


@router.post("/initiate", response_model=InitiateResponse)
def initiate(db: Session = Depends(get_db)):
    """Generate a new Quick Connect code. No authentication required."""
    entry = _new_code(db)
    return InitiateResponse(code=entry.code, poll_token=entry.poll_token, expires_at=entry.expires_at)


@router.post("/issue", response_model=InitiateResponse)
def issue(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Issue a code that is already authorized for the current user.

    This is the "Connect a phone" direction: the signed-in browser shows the
    code (as a QR) and the phone polls once with it to receive its JWT. The
    code carries the same TTL and single use as a typed one, and the issuer
    can cancel it early (DELETE below), which is what closing the QR does.
    """
    entry = _new_code(db)
    entry.user_id = current_user.id
    entry.authorized_at = datetime.utcnow()
    db.commit()

    ip = request.client.host if request.client else None
    audit(db, "auth.quick_connect_issued",
          user_id=current_user.id, username=current_user.username, ip=ip,
          details={"code": entry.code})
    return InitiateResponse(code=entry.code, poll_token=entry.poll_token, expires_at=entry.expires_at)


@router.get("/{code}", response_model=CodeStatusResponse)
def code_status(code: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """State of a code the current user owns. 404 once it is consumed, cancelled
    or expired, which is how the issuing browser learns the phone took it."""
    entry = _owned_live_code(db, code, current_user.id)
    return CodeStatusResponse(
        status="authorized" if entry.authorized_at is not None else "pending",
        expires_at=entry.expires_at,
    )


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def cancel(code: str, request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Invalidate a code the current user owns before it expires."""
    entry = _owned_live_code(db, code, current_user.id)
    db.delete(entry)
    db.commit()
    ip = request.client.host if request.client else None
    audit(db, "auth.quick_connect_cancelled",
          user_id=current_user.id, username=current_user.username, ip=ip,
          details={"code": entry.code})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post("/poll")
def poll(body: PollRequest, request: Request, db: Session = Depends(get_db)):
    """Poll for code authorization status. Returns JWT once authorized.

    Requires the poll_token issued by initiate — a guessed code alone gets the
    same 410 as an unknown one, so there is no oracle and nothing to steal.
    """
    entry = db.query(QuickConnectCode).filter(QuickConnectCode.code == body.code.upper().strip()).first()
    if not entry or not entry.poll_token or not hmac.compare_digest(entry.poll_token, body.poll_token):
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
