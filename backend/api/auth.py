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
        role="admin",
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
    """Latest KOReader sync info for the current user, considering both
    TomeSync (in-house plugin) and legacy KOSync clients.
    """
    from backend.models.kosync import KOSyncUser, KOSyncProgress
    from backend.models.tome_sync import ReadingSession, TomeSyncPosition

    # TomeSync side: most recent non-web reading session = last KOReader push
    ts_latest = (
        db.query(ReadingSession)
        .filter(ReadingSession.user_id == current_user.id)
        .filter((ReadingSession.device != "web") | (ReadingSession.device.is_(None)))
        .order_by(ReadingSession.started_at.desc())
        .first()
    )
    ts_count = (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == current_user.id)
        .count()
    )

    # Legacy KOSync side
    kosync_latest = None
    kosync_count = 0
    kosync_user = db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    if kosync_user:
        kosync_latest = (
            db.query(KOSyncProgress)
            .filter(KOSyncProgress.user_id == kosync_user.id)
            .order_by(KOSyncProgress.timestamp.desc())
            .first()
        )
        kosync_count = db.query(KOSyncProgress).filter(KOSyncProgress.user_id == kosync_user.id).count()

    # Pick whichever has the more recent timestamp
    candidates: list[tuple] = []
    if ts_latest:
        candidates.append((ts_latest.started_at, ts_latest.device))
    if kosync_latest:
        candidates.append((kosync_latest.timestamp, kosync_latest.device))

    if not candidates:
        return {"linked": False}

    candidates.sort(reverse=True, key=lambda x: x[0])
    last_sync, last_device = candidates[0]
    return {
        "linked": True,
        "synced_documents": max(ts_count, kosync_count),
        # Explicit UTC marker — naive ISO strings get parsed as local time
        # by JS, which would offset the relative-time display by the local TZ.
        "last_sync": last_sync.isoformat() + "Z" if last_sync else None,
        "last_device": last_device,
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


@router.get("/me/backup")
def export_my_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export all of the current user's data as a JSON download.

    Includes: profile, reading status, reading sessions, sync positions,
    shelves, and legacy KOSync progress/history. Excludes: API tokens,
    KOReader plugin keys, OPDS PINs, KOSync userkey (anything credential-
    bearing). Book content itself is not included — Tome only references
    the files on disk.

    Each book reference includes title, author, and content_hash so the
    export remains human-readable and restoration can match books across
    databases (where IDs may differ but content hashes don't).
    """
    from fastapi.responses import JSONResponse
    from datetime import datetime as _dt

    from backend import __version__
    from backend.models.book import Book, BookFile
    from backend.models.user_book_status import UserBookStatus
    from backend.models.tome_sync import ReadingSession, TomeSyncPosition
    from backend.models.library import SavedFilter
    from backend.models.kosync import (
        KOSyncUser,
        KOSyncProgress,
        KOSyncDocumentMap,
        ReadingHistory,
    )

    # Pre-load book metadata for every book the user has touched, so we can
    # decorate references with title/author/content_hash without N+1.
    touched_book_ids: set[int] = set()
    statuses = db.query(UserBookStatus).filter(UserBookStatus.user_id == current_user.id).all()
    touched_book_ids.update(s.book_id for s in statuses if s.book_id)
    sessions = (
        db.query(ReadingSession)
        .filter(ReadingSession.user_id == current_user.id)
        .order_by(ReadingSession.started_at)
        .all()
    )
    touched_book_ids.update(s.book_id for s in sessions if s.book_id)
    positions = (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == current_user.id)
        .all()
    )
    touched_book_ids.update(p.book_id for p in positions if p.book_id)

    book_index: dict[int, dict] = {}
    if touched_book_ids:
        books = db.query(Book).filter(Book.id.in_(touched_book_ids)).all()
        for b in books:
            first_file = b.files[0] if b.files else None
            book_index[b.id] = {
                "title": b.title,
                "author": b.author,
                "content_hash": first_file.content_hash if first_file else None,
            }

    def book_ref(book_id: int | None) -> dict:
        if book_id is None:
            return {"book_id": None}
        meta = book_index.get(book_id)
        return {
            "book_id": book_id,
            "book_title": meta.get("title") if meta else None,
            "book_author": meta.get("author") if meta else None,
            "book_content_hash": meta.get("content_hash") if meta else None,
        }

    shelves = (
        db.query(SavedFilter)
        .filter(SavedFilter.owner_id == current_user.id)
        .order_by(SavedFilter.sort_order, SavedFilter.id)
        .all()
    )

    # Legacy KOSync (if the user ever linked the upstream KOSync client)
    kosync_user = (
        db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    )
    kosync_progress_rows: list[KOSyncProgress] = []
    if kosync_user:
        kosync_progress_rows = (
            db.query(KOSyncProgress).filter(KOSyncProgress.user_id == kosync_user.id).all()
        )
    kosync_doc_map = (
        db.query(KOSyncDocumentMap)
        .filter(KOSyncDocumentMap.tome_user_id == current_user.id)
        .all()
    )
    reading_history = (
        db.query(ReadingHistory)
        .filter(ReadingHistory.user_id == current_user.id)
        .order_by(ReadingHistory.synced_at)
        .all()
    )

    payload = {
        "schema_version": 1,
        "tome_version": __version__,
        "exported_at": _dt.utcnow().isoformat() + "Z",
        "user": {
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "is_admin": current_user.is_admin,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        },
        "reading_status": [
            {
                **book_ref(s.book_id),
                "status": s.status,
                "progress_pct": s.progress_pct,
                "cfi": s.cfi,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in statuses
        ],
        "reading_sessions": [
            {
                "id": s.id,
                **book_ref(s.book_id),
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_seconds": s.duration_seconds,
                "progress_start": s.progress_start,
                "progress_end": s.progress_end,
                "pages_turned": s.pages_turned,
                "device": s.device,
                "session_uuid": s.session_uuid,
            }
            for s in sessions
        ],
        "sync_positions": [
            {
                **book_ref(p.book_id),
                "progress": p.progress,
                "percentage": p.percentage,
                "device": p.device,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in positions
        ],
        "shelves": [
            {
                "name": sh.name,
                "icon": sh.icon,
                "params": sh.params,
                "sort_order": sh.sort_order,
                "created_at": sh.created_at.isoformat() if sh.created_at else None,
            }
            for sh in shelves
        ],
        "kosync_legacy": {
            "linked": kosync_user is not None,
            "progress": [
                {
                    "document": p.document,
                    "progress": p.progress,
                    "percentage": p.percentage,
                    "device": p.device,
                    "device_id": p.device_id,
                    "timestamp": p.timestamp,
                }
                for p in kosync_progress_rows
            ],
            "document_map": [
                {"document": m.document, **book_ref(m.book_id)}
                for m in kosync_doc_map
            ],
            "history": [
                {
                    **book_ref(h.book_id),
                    "document": h.document,
                    "percentage": h.percentage,
                    "device": h.device,
                    "synced_at": h.synced_at.isoformat() if h.synced_at else None,
                }
                for h in reading_history
            ],
        },
    }

    date_str = _dt.utcnow().strftime("%Y-%m-%d")
    filename = f"tome-backup-{current_user.username}-{date_str}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
