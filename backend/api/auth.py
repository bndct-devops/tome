from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import Response
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.models.user import User, UserPermission
from backend.schemas.auth import LoginRequest, SetupRequest, TokenResponse, UserOut
from backend.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/setup-needed")
def setup_needed(db: Session = Depends(get_db)):
    """Returns whether first-run setup is required (no users exist yet)."""
    count = db.query(User).count()
    return {"setup_needed": count == 0}


@router.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def setup(body: SetupRequest, db: Session = Depends(get_db)):
    """Create the first admin account. Only works when no users exist."""
    if db.query(User).count() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed. Use the normal login.",
        )

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    db.flush()  # get the user id

    # Admin gets all permissions
    perms = UserPermission(
        user_id=user.id,
        can_upload=True,
        can_download=True,
        can_edit_metadata=True,
        can_delete_books=True,
        can_manage_libraries=True,
        can_manage_tags=True,
        can_manage_series=True,
        can_manage_users=True,
        can_approve_bindery=True,
        can_view_stats=True,
        can_use_opds=True,
        can_use_kosync=True,
        can_share=True,
        can_bulk_operations=True,
    )
    db.add(perms)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login with username or email + password. Returns a JWT."""
    ip = request.client.host if request.client else None
    user = (
        db.query(User)
        .filter((User.username == body.username) | (User.email == body.username))
        .first()
    )
    if not user or not verify_password(body.password, user.hashed_password):
        audit(db, "auth.login_failed",
              username=body.username, ip=ip,
              details={"reason": "invalid credentials"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        audit(db, "auth.login_failed",
              user_id=user.id, username=user.username, ip=ip,
              details={"reason": "account disabled"})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    token = create_access_token(user.id)
    audit(db, "auth.login", user_id=user.id, username=user.username, ip=ip)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return current_user


class UpdateProfileRequest(PydanticBaseModel):
    username: str | None = None
    email: str | None = None


@router.put("/me", response_model=UserOut)
def update_profile(
    body: UpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.username is not None and body.username != current_user.username:
        if db.query(User).filter(User.username == body.username).first():
            raise HTTPException(status_code=409, detail="Username already taken")
        current_user.username = body.username
    if body.email is not None and body.email != current_user.email:
        if db.query(User).filter(User.email == body.email).first():
            raise HTTPException(status_code=409, detail="Email already taken")
        current_user.email = body.email
    db.commit()
    db.refresh(current_user)
    return current_user


class ChangePasswordRequest(PydanticBaseModel):
    current_password: str
    new_password: str


@router.put("/me/password", status_code=204)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    current_user.hashed_password = hash_password(body.new_password)
    current_user.must_change_password = False
    db.commit()
    audit(db, "auth.password_changed", user_id=current_user.id, username=current_user.username)
    return Response(status_code=204)


@router.get("/me/kosync")
def my_kosync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.models.kosync import KOSyncUser, KOSyncProgress
    kosync_user = db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    if not kosync_user:
        return {"linked": False}
    latest = (
        db.query(KOSyncProgress)
        .filter(KOSyncProgress.user_id == kosync_user.id)
        .order_by(KOSyncProgress.timestamp.desc())
        .first()
    )
    count = db.query(KOSyncProgress).filter(KOSyncProgress.user_id == kosync_user.id).count()
    return {
        "linked": True,
        "synced_documents": count,
        "last_sync": latest.timestamp if latest else None,
        "last_device": latest.device if latest else None,
    }


@router.post("/me/kosync", status_code=201)
def register_kosync(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register or re-link a KOSync user from the web UI."""
    import hashlib
    from backend.models.kosync import KOSyncUser
    password = str(body.get("password", "")).strip()
    if not password:
        raise HTTPException(status_code=400, detail="Password required")
    userkey = hashlib.md5(password.encode()).hexdigest()

    existing = db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    if existing:
        existing.userkey = userkey
        existing.user_id = current_user.id
    else:
        db.add(KOSyncUser(username=current_user.username, userkey=userkey, user_id=current_user.id))
    db.commit()
    return {"username": current_user.username}


@router.get("/me/stats")
def my_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.models.user_book_status import UserBookStatus
    from backend.models.book import Book
    statuses = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id
    ).all()
    counts: dict[str, int] = {"unread": 0, "reading": 0, "read": 0}
    for s in statuses:
        if s.status in counts:
            counts[s.status] += 1
    total_books = db.query(Book).filter(Book.status == "active").count()
    counts["total"] = total_books
    counts["untracked"] = total_books - sum(v for k, v in counts.items() if k not in ("total", "untracked"))
    return counts
