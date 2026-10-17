from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, HTTPBasic, HTTPBasicCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


@lru_cache(maxsize=1)
def _signing_key() -> str:
    return settings.resolve_secret_key()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_access_token(subject: str | int, device_token_id: str | None = None) -> str:
    """JWT for a user. With `device_token_id` the token is bound to a
    ClientDevice row (as `jti`) and carries no `exp`: it lives until that row
    is revoked (every login is one, see services/client_devices.py). Every
    other token (admin impersonation) expires after `jwt_expire_minutes`."""
    payload: dict = {"sub": str(subject)}
    if device_token_id:
        payload["jti"] = device_token_id
    else:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(payload, _signing_key(), algorithm=settings.jwt_algorithm)


def decode_claims(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _signing_key(), algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def decode_token(token: str) -> Optional[str]:
    claims = decode_claims(token)
    return claims.get("sub") if claims else None


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    from backend.models.user import User
    from backend.models.api_token import ApiToken

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token.startswith("tome_"):
        import hashlib
        from datetime import datetime as _dt
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        api_token = (
            db.query(ApiToken)
            .filter(ApiToken.token_hash == token_hash, ApiToken.revoked_at.is_(None))
            .first()
        )
        if api_token is None:
            raise credentials_exc
        user = db.query(User).filter(User.id == api_token.user_id).first()
        if user is None or not user.is_active:
            raise credentials_exc
        # Update last_used_at (fire-and-forget)
        try:
            api_token.last_used_at = _dt.utcnow()
            db.commit()
        except Exception:
            db.rollback()
        if getattr(api_token, "scope", "full") == "readonly" and request.method not in ("GET", "HEAD", "OPTIONS"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This token is read-only",
            )
        request.state.api_token_id = api_token.id
        return user

    resolved = resolve_jwt(db, token)
    if resolved is None:
        raise credentials_exc
    user, device = resolved
    if device is not None:
        request.state.device_id = device.id
    return user


def resolve_jwt(db: Session, token: str):
    """(user, device) for a valid JWT of an active user, else None. `device`
    is the ClientDevice a device-bound token belongs to (None for a plain,
    expiring token); a revoked or unknown device makes the token invalid.
    Every JWT check goes through here, including the `?token=` reader routes."""
    from backend.models.user import User

    claims = decode_claims(token)
    try:
        user_id = int(claims["sub"]) if claims and claims.get("sub") is not None else None
    except (TypeError, ValueError):
        user_id = None
    if user_id is None:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        return None

    # Device-bound token: the row must exist, belong to this user and not be revoked.
    jti = claims.get("jti")
    if not jti:
        return user, None
    from backend.models.client_device import ClientDevice
    device = db.query(ClientDevice).filter(ClientDevice.token_id == jti).first()
    if device is None or device.revoked_at is not None or device.user_id != user.id:
        return None
    now = datetime.utcnow()
    if web_session_idle(device, now):
        # Unused too long: sign it out for good and drop it from the list.
        try:
            db.delete(device)
            db.commit()
        except Exception:
            db.rollback()
        return None
    if device.last_seen_at is None or now - device.last_seen_at > timedelta(minutes=5):
        try:
            device.last_seen_at = now
            db.commit()
        except Exception:
            db.rollback()
    return user, device


def web_session_idle(device, now: datetime | None = None) -> bool:
    """True for a browser sign-in unused for `web_session_idle_days`."""
    days = settings.web_session_idle_days
    if device.kind != "web" or days <= 0:
        return False
    seen = device.last_seen_at or device.created_at
    return (now or datetime.utcnow()) - seen > timedelta(days=days)


def user_from_query_token(db: Session, token: str):
    """The user behind a `?token=` query param (img/epub.js/pdf.js fetches
    cannot send an Authorization header). 401 when the token is not valid."""
    resolved = resolve_jwt(db, token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return resolved[0]


async def get_current_admin(user=Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


_http_basic = HTTPBasic(auto_error=False)

def get_current_user_basic(
    credentials: Optional[HTTPBasicCredentials] = Depends(_http_basic),
    db: Session = Depends(get_db),
):
    """HTTP Basic Auth dependency for OPDS endpoints."""
    from backend.models.user import User

    _unauth = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": 'Basic realm="Tome OPDS"'},
    )
    if not credentials:
        raise _unauth

    user = db.query(User).filter(User.username == credentials.username).first()
    if not user or not user.is_active:
        raise _unauth

    password_ok = bcrypt.checkpw(credentials.password.encode(), user.hashed_password.encode())
    if not password_ok:
        # Fall back to checking OPDS PINs
        from backend.models.opds_pin import OpdsPin
        from datetime import datetime as _dt
        matched_pin = None
        pins = db.query(OpdsPin).filter(OpdsPin.user_id == user.id).all()
        for pin in pins:
            if bcrypt.checkpw(credentials.password.encode(), pin.hashed_pin.encode()):
                matched_pin = pin
                break
        if matched_pin is None:
            raise _unauth
        matched_pin.last_used_at = _dt.utcnow()
        db.commit()

    return user
