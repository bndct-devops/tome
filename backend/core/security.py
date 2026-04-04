from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, HTTPBasic, HTTPBasicCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_access_token(subject: str | int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    from backend.models.user import User

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_id = decode_token(token)
    if user_id is None:
        raise credentials_exc

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exc
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

    if user.permissions and not user.permissions.can_use_opds:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="OPDS access disabled")
    return user
