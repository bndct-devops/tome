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
    is revoked. Every other token expires after `jwt_expire_minutes`."""
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

    claims = decode_claims(token)
    user_id = claims.get("sub") if claims else None
    if user_id is None:
        raise credentials_exc

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exc

    # Device-bound token: the row must exist, belong to this user and not be revoked.
    jti = claims.get("jti")
    if jti:
        from backend.models.client_device import ClientDevice
        device = db.query(ClientDevice).filter(ClientDevice.token_id == jti).first()
        if device is None or device.revoked_at is not None or device.user_id != user.id:
            raise credentials_exc
        now = datetime.utcnow()
        if device.last_seen_at is None or now - device.last_seen_at > timedelta(minutes=5):
            try:
                device.last_seen_at = now
                db.commit()
            except Exception:
                db.rollback()
        request.state.device_id = device.id
    return user


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
