from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.services.audit import audit

router = APIRouter()


# ── Reading status ──────────────────────────────────────────────────────────

class StatusOut(BaseModel):
    book_id: int
    status: str
    progress_pct: Optional[float]
    cfi: Optional[str] = None
    updated_at: Optional[str]

    model_config = {"from_attributes": True}


class StatusIn(BaseModel):
    status: str
    progress_pct: Optional[float] = None
    cfi: Optional[str] = None


@router.get("/books/{book_id}/status", response_model=StatusOut)
def get_book_status(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(UserBookStatus).filter_by(user_id=current_user.id, book_id=book_id).first()
    if not row:
        return StatusOut(book_id=book_id, status="unread", progress_pct=None, cfi=None, updated_at=None)
    return StatusOut(
        book_id=row.book_id,
        status=row.status,
        progress_pct=row.progress_pct,
        cfi=row.cfi,
        updated_at=str(row.updated_at),
    )


@router.put("/books/{book_id}/status", response_model=StatusOut)
def set_book_status(
    book_id: int,
    body: StatusIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.status not in ("unread", "reading", "read"):
        raise HTTPException(400, "status must be unread, reading, or read")
    from backend.models.book import Book
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    row = db.query(UserBookStatus).filter_by(user_id=current_user.id, book_id=book_id).first()
    # Only overwrite progress_pct/cfi when explicitly sent (not just defaulting to None)
    raw = body.model_dump(exclude_unset=True)
    if row:
        row.status = body.status
        if body.status == 'unread':
            row.progress_pct = None
            row.cfi = None
        else:
            if 'progress_pct' in raw:
                row.progress_pct = body.progress_pct
            if 'cfi' in raw:
                row.cfi = body.cfi
        row.updated_at = datetime.utcnow()
    else:
        row = UserBookStatus(
            user_id=current_user.id,
            book_id=book_id,
            status=body.status,
            progress_pct=body.progress_pct,
            cfi=body.cfi,
            updated_at=datetime.utcnow(),
        )
        db.add(row)
    db.commit()
    db.refresh(row)

    # ── Sync position to TomeSyncPosition (for KOReader pickup) ─────────────
    if body.progress_pct is not None or body.cfi is not None:
        pos = db.query(TomeSyncPosition).filter(
            TomeSyncPosition.user_id == current_user.id,
            TomeSyncPosition.book_id == book_id,
        ).first()
        if pos:
            if body.progress_pct is not None:
                pos.percentage = body.progress_pct / 100.0
            if body.cfi is not None:
                pos.progress = body.cfi
            pos.device = "web"
            pos.updated_at = datetime.utcnow()
        else:
            db.add(TomeSyncPosition(
                user_id=current_user.id,
                book_id=book_id,
                percentage=(body.progress_pct or 0) / 100.0,
                progress=body.cfi,
                device="web",
            ))
        db.commit()

    # ── Web reader session tracking ──────────────────────────────────────────
    _track_web_reading_session(
        db=db,
        user_id=current_user.id,
        book_id=book_id,
        status=body.status,
        progress_pct=body.progress_pct,
    )

    return StatusOut(
        book_id=row.book_id,
        status=row.status,
        progress_pct=row.progress_pct,
        cfi=row.cfi,
        updated_at=str(row.updated_at),
    )


def _track_web_reading_session(
    *,
    db: Session,
    user_id: int,
    book_id: int,
    status: str,
    progress_pct: Optional[float],
) -> None:
    """Create or extend a ReadingSession for the web reader.

    Called after every status update from the browser.  The web reader
    calls PUT /api/books/{book_id}/status every ~1.5 s while the book is
    open, so we treat any call within the last 5 minutes as a continuation
    of the same session rather than a new one.
    """
    now: datetime = datetime.utcnow()
    cutoff: datetime = now - timedelta(minutes=5)

    if status == "reading" and progress_pct is not None:
        progress_fraction: float = progress_pct / 100.0

        # Look for a recent open session to extend
        recent: Optional[ReadingSession] = (
            db.query(ReadingSession)
            .filter(
                ReadingSession.user_id == user_id,
                ReadingSession.book_id == book_id,
                ReadingSession.device == "web",
                ReadingSession.ended_at >= cutoff,
            )
            .order_by(ReadingSession.ended_at.desc())
            .first()
        )

        if recent is not None:
            recent.ended_at = now
            recent.duration_seconds = int((recent.ended_at - recent.started_at).total_seconds())
            recent.progress_end = progress_fraction
        else:
            db.add(
                ReadingSession(
                    user_id=user_id,
                    book_id=book_id,
                    started_at=now,
                    ended_at=now,
                    duration_seconds=0,
                    progress_start=progress_fraction,
                    progress_end=progress_fraction,
                    pages_turned=None,
                    device="web",
                )
            )
        db.commit()

    elif status == "read":
        # Book marked as finished — close any dangling session
        recent = (
            db.query(ReadingSession)
            .filter(
                ReadingSession.user_id == user_id,
                ReadingSession.book_id == book_id,
                ReadingSession.device == "web",
                ReadingSession.ended_at >= cutoff,
            )
            .order_by(ReadingSession.ended_at.desc())
            .first()
        )

        if recent is not None:
            recent.ended_at = now
            recent.duration_seconds = int((recent.ended_at - recent.started_at).total_seconds())
            db.commit()


# ── KOSync progress linking ──────────────────────────────────────────────────

class KOSyncProgressOut(BaseModel):
    document: str
    percentage: float
    device: Optional[str]
    timestamp: int


class KOSyncStatusOut(BaseModel):
    linked: bool
    percentage: Optional[float] = None
    device: Optional[str] = None
    timestamp: Optional[int] = None
    unlinked_documents: list[KOSyncProgressOut] = []


@router.get("/books/{book_id}/kosync-progress", response_model=KOSyncStatusOut)
def get_book_kosync_progress(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.models.kosync import KOSyncUser, KOSyncProgress, KOSyncDocumentMap

    kosync_user = db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    if not kosync_user:
        return KOSyncStatusOut(linked=False)

    # Check if this book is already linked to a document
    doc_map = db.query(KOSyncDocumentMap).filter(
        KOSyncDocumentMap.tome_user_id == current_user.id,
        KOSyncDocumentMap.book_id == book_id,
    ).first()

    if doc_map:
        progress = db.query(KOSyncProgress).filter(
            KOSyncProgress.user_id == kosync_user.id,
            KOSyncProgress.document == doc_map.document,
        ).first()
        if progress:
            return KOSyncStatusOut(
                linked=True,
                percentage=progress.percentage,
                device=progress.device,
                timestamp=progress.timestamp,
            )
        # Linked but no matching progress — fall through to show unlinked docs

    # Return all unlinked documents (not mapped to any book for this user)
    all_progress = db.query(KOSyncProgress).filter(
        KOSyncProgress.user_id == kosync_user.id,
    ).all()

    mapped_docs = {
        m.document for m in db.query(KOSyncDocumentMap).filter(
            KOSyncDocumentMap.tome_user_id == current_user.id,
        ).all()
    }

    unlinked = [
        KOSyncProgressOut(
            document=p.document,
            percentage=p.percentage,
            device=p.device,
            timestamp=p.timestamp,
        )
        for p in all_progress
        if p.document not in mapped_docs
    ]

    return KOSyncStatusOut(linked=False, unlinked_documents=unlinked)


class LinkKOSyncBody(BaseModel):
    document: str


@router.post("/books/{book_id}/link-kosync", status_code=200)
def link_kosync_document(
    book_id: int,
    body: LinkKOSyncBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.models.book import Book
    from backend.models.kosync import KOSyncUser, KOSyncProgress, KOSyncDocumentMap

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")

    kosync_user = db.query(KOSyncUser).filter(KOSyncUser.username == current_user.username).first()
    if not kosync_user:
        raise HTTPException(400, "No KOSync account linked")

    progress = db.query(KOSyncProgress).filter(
        KOSyncProgress.user_id == kosync_user.id,
        KOSyncProgress.document == body.document,
    ).first()
    if not progress:
        raise HTTPException(404, "Document not found in sync history")

    # Upsert document map
    existing = db.query(KOSyncDocumentMap).filter(
        KOSyncDocumentMap.tome_user_id == current_user.id,
        KOSyncDocumentMap.document == body.document,
    ).first()
    if existing:
        existing.book_id = book_id
    else:
        db.add(KOSyncDocumentMap(
            tome_user_id=current_user.id,
            document=body.document,
            book_id=book_id,
        ))

    # Update UserBookStatus
    pct = progress.percentage
    new_status = "read" if pct >= 0.95 else "reading"
    ubs = db.query(UserBookStatus).filter_by(user_id=current_user.id, book_id=book_id).first()
    if ubs:
        ubs.progress_pct = pct
        ubs.status = new_status
    else:
        db.add(UserBookStatus(
            user_id=current_user.id,
            book_id=book_id,
            status=new_status,
            progress_pct=pct,
        ))

    db.commit()
    return {"ok": True}


# ── User management (admin only) ────────────────────────────────────────────

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    require_role(current_user, "admin")
    return current_user


class PermissionsSchema(BaseModel):
    can_upload: bool = False
    can_download: bool = True
    can_edit_metadata: bool = False
    can_delete_books: bool = False
    can_manage_libraries: bool = False
    can_manage_tags: bool = False
    can_manage_series: bool = False
    can_manage_users: bool = False
    can_approve_bindery: bool = False
    can_view_stats: bool = True
    can_use_opds: bool = True
    can_use_kosync: bool = True
    can_share: bool = False
    can_bulk_operations: bool = False

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    role: str
    created_at: str
    permissions: Optional[PermissionsSchema]

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    is_admin: bool = False
    role: str = "guest"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    role: Optional[str] = None


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at).all()
    result = []
    for u in users:
        perms = u.permissions
        result.append(
            UserOut(
                id=u.id,
                username=u.username,
                email=u.email,
                is_active=u.is_active,
                is_admin=u.is_admin,
                role="admin" if u.is_admin else u.role,
                created_at=str(u.created_at),
                permissions=PermissionsSchema.model_validate(perms) if perms else None,
            )
        )
    return result


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "Username already taken")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already taken")
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    # Keep is_admin and role in sync
    effective_is_admin = body.is_admin or body.role == "admin"
    effective_role = "admin" if effective_is_admin else body.role
    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hashed,
        is_admin=effective_is_admin,
        role=effective_role,
        must_change_password=True,  # Force password change on first login
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id)
    db.add(perms)
    db.commit()
    db.refresh(user)
    audit(db, "users.created", user_id=admin.id, username=admin.username,
          resource_type="user", resource_id=user.id, resource_title=user.username,
          details={"is_admin": user.is_admin, "role": user.role})
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        role="admin" if user.is_admin else user.role,
        created_at=str(user.created_at),
        permissions=PermissionsSchema.model_validate(user.permissions),
    )


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if body.username is not None:
        user.username = body.username
    if body.email is not None:
        user.email = body.email
    if body.password is not None:
        user.hashed_password = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    if body.is_active is not None:
        user.is_active = body.is_active
    # Keep is_admin and role in sync
    if body.role is not None and body.is_admin is None:
        # role changed without explicit is_admin — derive is_admin from new role
        user.role = body.role
        user.is_admin = body.role == "admin"
    elif body.is_admin is not None and body.role is None:
        # is_admin changed without explicit role — derive role from is_admin
        user.is_admin = body.is_admin
        user.role = "admin" if body.is_admin else ("guest" if user.role == "admin" else user.role)
    elif body.is_admin is not None and body.role is not None:
        # Both specified — honour both; role wins for the role field
        user.is_admin = body.is_admin or body.role == "admin"
        user.role = "admin" if user.is_admin else body.role
    db.commit()
    db.refresh(user)
    audit(db, "users.updated", user_id=admin.id, username=admin.username,
          resource_type="user", resource_id=user.id, resource_title=user.username,
          details=body.model_dump(exclude_unset=True))
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        role="admin" if user.is_admin else user.role,
        created_at=str(user.created_at),
        permissions=PermissionsSchema.model_validate(user.permissions) if user.permissions else None,
    )


@router.post("/users/{user_id}/impersonate")
def impersonate_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot impersonate yourself")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if not target.is_active:
        raise HTTPException(400, "Cannot impersonate an inactive user")
    from backend.core.security import create_access_token
    token = create_access_token(target.id)
    audit(db, "auth.impersonated", user_id=admin.id, username=admin.username,
          resource_type="user", resource_id=target.id, resource_title=target.username)
    return {"access_token": token, "username": target.username}


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    audit(db, "users.deleted", user_id=admin.id, username=admin.username,
          resource_type="user", resource_id=user.id, resource_title=user.username)
    db.delete(user)
    db.commit()


@router.put("/users/{user_id}/permissions", response_model=UserOut)
def set_permissions(
    user_id: int,
    body: PermissionsSchema,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    perms = user.permissions
    if not perms:
        perms = UserPermission(user_id=user.id)
        db.add(perms)
    for field, val in body.model_dump().items():
        setattr(perms, field, val)
    db.commit()
    db.refresh(user)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        role="admin" if user.is_admin else user.role,
        created_at=str(user.created_at),
        permissions=PermissionsSchema.model_validate(user.permissions),
    )


@router.get("/admin/sync-status")
def get_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    require_role(current_user, "admin")
    from backend.models.book import Book
    from backend.models.tome_sync import TomeSyncPosition
    from sqlalchemy import or_

    # Fetch all UserBookStatus rows that are not "unread"
    ubs_rows = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.status != "unread")
        .all()
    )

    # Fetch all TomeSyncPosition rows
    tsp_rows = db.query(TomeSyncPosition).all()

    # Build a combined key set: (user_id, book_id)
    keys: set[tuple[int, int]] = set()
    for r in ubs_rows:
        keys.add((r.user_id, r.book_id))
    for r in tsp_rows:
        keys.add((r.user_id, r.book_id))

    # Index for fast lookup
    ubs_index: dict[tuple[int, int], UserBookStatus] = {(r.user_id, r.book_id): r for r in ubs_rows}
    tsp_index: dict[tuple[int, int], TomeSyncPosition] = {(r.user_id, r.book_id): r for r in tsp_rows}

    # Preload books and users
    book_ids = {k[1] for k in keys}
    user_ids = {k[0] for k in keys}
    books: dict[int, Book] = {b.id: b for b in db.query(Book).filter(Book.id.in_(book_ids)).all()}
    users: dict[int, User] = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}

    result = []
    for user_id, book_id in keys:
        book = books.get(book_id)
        user = users.get(user_id)
        if not book or not user:
            continue

        ubs = ubs_index.get((user_id, book_id))
        tsp = tsp_index.get((user_id, book_id))

        status = ubs.status if ubs else "unread"
        progress_pct = tsp.percentage if tsp else (ubs.progress_pct if ubs else None)
        last_synced = (tsp.updated_at if tsp else (ubs.updated_at if ubs else None))
        device = tsp.device if tsp else None
        source = "tomesync" if tsp else "web"

        result.append({
            "book_id": book.id,
            "book_title": book.title,
            "book_author": book.author,
            "book_series": book.series,
            "book_series_index": book.series_index,
            "user_id": user.id,
            "username": user.username,
            "status": status,
            "progress_pct": progress_pct,
            "last_synced": last_synced.isoformat() + "Z" if last_synced else None,
            "device": device,
            "source": source,
        })

    result.sort(key=lambda x: x["last_synced"] or "", reverse=True)
    return result


@router.delete("/admin/sync-status/{user_id}/{book_id}", status_code=204)
def delete_sync_record(
    user_id: int,
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    from backend.models.tome_sync import TomeSyncPosition, ReadingSession

    tsp = db.query(TomeSyncPosition).filter(
        TomeSyncPosition.user_id == user_id, TomeSyncPosition.book_id == book_id
    ).first()
    if tsp:
        db.query(ReadingSession).filter(
            ReadingSession.user_id == user_id, ReadingSession.book_id == book_id
        ).delete()
        db.delete(tsp)

    ubs = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == user_id, UserBookStatus.book_id == book_id
    ).first()
    if ubs:
        db.delete(ubs)

    db.commit()


@router.get("/admin/audit-logs")
def get_audit_logs(
    page: int = 1,
    per_page: int = 50,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_role(current_user, "admin")
    from backend.models.audit_log import AuditLog
    from datetime import datetime, timedelta
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action.like(f"{action}%"))
    if from_date:
        q = q.filter(AuditLog.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.filter(AuditLog.created_at < datetime.fromisoformat(to_date) + timedelta(days=1))
    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "resource_title": r.resource_title,
                "details": r.details,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/admin/stats")
def get_admin_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_role(current_user, "admin")
    from backend.models.book import Book
    from backend.core.config import settings
    import os
    book_count = db.query(Book).count()
    user_count = db.query(User).count()
    db_path = os.path.join(settings.data_dir, "tome.db")
    db_size_mb = round(os.path.getsize(db_path) / 1024 / 1024, 2) if os.path.exists(db_path) else 0
    covers_dir = os.path.join(settings.data_dir, "covers")
    covers_count = len(os.listdir(covers_dir)) if os.path.exists(covers_dir) else 0
    covers_size_mb = round(
        sum(os.path.getsize(os.path.join(covers_dir, f)) for f in os.listdir(covers_dir) if os.path.isfile(os.path.join(covers_dir, f))) / 1024 / 1024, 2
    ) if os.path.exists(covers_dir) else 0
    import sys
    return {
        "book_count": book_count,
        "user_count": user_count,
        "db_size_mb": db_size_mb,
        "covers_count": covers_count,
        "covers_size_mb": covers_size_mb,
        "library_dir": str(settings.library_dir),
        "data_dir": str(settings.data_dir),
        "incoming_dir": str(settings.incoming_dir),
        "tome_version": "0.1.0",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


@router.delete("/admin/covers-cache")
def clear_covers_cache(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_role(current_user, "admin")
    import shutil, os
    from backend.core.config import settings
    covers_dir = os.path.join(settings.data_dir, "covers")
    deleted = 0
    if os.path.exists(covers_dir):
        for f in os.listdir(covers_dir):
            fp = os.path.join(covers_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)
                deleted += 1
    return {"deleted": deleted}
