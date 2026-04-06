from fastapi import HTTPException
from backend.models.user import User

ROLE_ORDER = {"guest": 0, "member": 1, "admin": 2}


def _effective_role(user: User) -> str:
    """Return the effective role, honouring is_admin as an override."""
    return "admin" if user.is_admin else user.role


def require_role(user: User, minimum: str) -> None:
    """Raise 403 if user's role is below the minimum required."""
    if ROLE_ORDER.get(_effective_role(user), 0) < ROLE_ORDER[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def has_role(user: User, minimum: str) -> bool:
    """Check if user meets minimum role without raising."""
    return ROLE_ORDER.get(_effective_role(user), 0) >= ROLE_ORDER[minimum]


def is_admin(user: User) -> bool:
    return user.is_admin or user.role == "admin"


def is_member_or_above(user: User) -> bool:
    return has_role(user, "member")
