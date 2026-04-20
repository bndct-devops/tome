"""API Token management endpoints.

Allows users to create, list, and revoke personal API tokens.
Tokens use the format: tome_<random> and are stored as sha256 hashes.
"""
import hashlib
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.api_token import ApiToken
from backend.models.user import User
from backend.services.audit import audit

router = APIRouter(prefix="/tokens", tags=["api-tokens"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class TokenCreateRequest(BaseModel):
    name: str


class TokenCreateResponse(BaseModel):
    id: int
    name: str
    prefix: str
    token: str
    created_at: datetime


class TokenListItem(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    user_id: int
    username: str

    model_config = {"from_attributes": False}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_request_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _get_api_token_id(request: Request) -> Optional[int]:
    return getattr(request.state, "api_token_id", None)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/", response_model=TokenCreateResponse, status_code=status.HTTP_201_CREATED)
def create_token(
    body: TokenCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new API token. Returns plaintext token exactly once."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Token name must not be empty")

    # Generate: "tome_" + 32-char urlsafe random (secrets.token_urlsafe(24) ≈ 32 chars)
    random_part = secrets.token_urlsafe(24)
    plaintext = f"tome_{random_part}"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    prefix = random_part[:8]

    token = ApiToken(
        user_id=current_user.id,
        name=name,
        token_hash=token_hash,
        prefix=prefix,
        created_at=datetime.utcnow(),
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    audit(
        db,
        "api_token.create",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="api_token",
        resource_id=token.id,
        resource_title=name,
        details={"prefix": prefix, "api_token_id": _get_api_token_id(request)},
        ip=_get_request_ip(request),
    )

    return TokenCreateResponse(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        token=plaintext,
        created_at=token.created_at,
    )


@router.get("/", response_model=list[TokenListItem])
def list_tokens(
    all: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List API tokens. Admin-only ?all=true returns every user's tokens."""
    if all and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    query = db.query(ApiToken).options(joinedload(ApiToken.user))
    if not all:
        query = query.filter(ApiToken.user_id == current_user.id)

    tokens = query.order_by(ApiToken.created_at.desc()).all()
    return [
        TokenListItem(
            id=t.id,
            name=t.name,
            prefix=t.prefix,
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            revoked_at=t.revoked_at,
            user_id=t.user_id,
            username=t.user.username,
        )
        for t in tokens
    ]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-revoke a token. Owner or admin only."""
    token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    if token.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    if token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        db.commit()

        audit(
            db,
            "api_token.revoke",
            user_id=current_user.id,
            username=current_user.username,
            resource_type="api_token",
            resource_id=token.id,
            resource_title=token.name,
            details={"api_token_id": _get_api_token_id(request)},
            ip=_get_request_ip(request),
        )
