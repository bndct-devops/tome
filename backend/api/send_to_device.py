"""Send-to-device endpoints: device CRUD, single/bulk send, admin SMTP status."""
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import require_role, user_can_see_book
from backend.core.security import get_current_user
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookFile
from backend.models.user import User
from backend.models.user_device import UserDevice
from backend.services.audit import audit
from backend.services.email import (
    FileTooLargeError,
    SmtpNotConfiguredError,
    SmtpSendError,
    send_book_to_device,
    send_books_bulk,
    send_test_email,
)
from backend.services.metadata_embed import get_baked_path
from backend.services.organizer import koreader_style_name

log = logging.getLogger(__name__)

router = APIRouter(tags=["send-to-device"])

FORMAT_PREFERENCE = ["epub", "pdf", "mobi", "cbz", "cbr"]


# ── Schemas ──────────────────────────────────────────────────────────────────

class DeviceOut(BaseModel):
    id: int
    name: str
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceCreate(BaseModel):
    name: str
    email: EmailStr


class SendRequest(BaseModel):
    device_id: int
    file_id: int


class BulkSendRequest(BaseModel):
    book_ids: list[int]
    device_id: int


class BulkSendResponse(BaseModel):
    sent: int
    failed: int
    errors: list[dict]


class SmtpStatusPublic(BaseModel):
    configured: bool


class SmtpStatusAdmin(BaseModel):
    configured: bool
    host: str | None
    port: int
    from_address: str | None


class SmtpTestRequest(BaseModel):
    email: EmailStr


class AdminDeviceOut(BaseModel):
    id: int
    username: str
    device_name: str
    device_email: str
    created_at: datetime


class SendHistoryEntry(BaseModel):
    id: int
    username: str | None
    book_title: str | None
    device_email: str | None
    device_name: str | None
    status: str | None
    format: str | None
    created_at: datetime


# ── Helpers ──────────────────────────────────────────────────────────────────

def _check_daily_limit(db: Session, user_id: int) -> None:
    if settings.smtp_daily_limit == 0:
        return
    today_start = datetime.combine(date.today(), datetime.min.time())
    count = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id == user_id,
            AuditLog.action == "books.sent_to_device",
            AuditLog.created_at >= today_start,
        )
        .count()
    )
    if count >= settings.smtp_daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily send limit reached ({settings.smtp_daily_limit}/day). Try again tomorrow.",
        )


def _get_best_file(book: Book) -> BookFile | None:
    if not book.files:
        return None
    by_format = {f.format: f for f in book.files}
    for fmt in FORMAT_PREFERENCE:
        if fmt in by_format:
            return by_format[fmt]
    return book.files[0]


# ── Device CRUD ──────────────────────────────────────────────────────────────

@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, "member")
    return (
        db.query(UserDevice)
        .filter(UserDevice.user_id == user.id)
        .order_by(UserDevice.created_at)
        .all()
    )


@router.post("/devices", response_model=DeviceOut, status_code=201)
def add_device(
    body: DeviceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, "member")

    count = db.query(UserDevice).filter(UserDevice.user_id == user.id).count()
    if count >= 10:
        raise HTTPException(400, "Maximum 10 devices allowed")

    name = body.name.strip()[:100]
    if not name:
        raise HTTPException(400, "Device name is required")

    existing = (
        db.query(UserDevice)
        .filter(UserDevice.user_id == user.id, UserDevice.email == body.email)
        .first()
    )
    if existing:
        raise HTTPException(400, "A device with this email already exists")

    device = UserDevice(user_id=user.id, name=name, email=body.email)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, "member")
    device = db.query(UserDevice).filter(UserDevice.id == device_id).first()
    if not device:
        raise HTTPException(404, "Device not found")
    if device.user_id != user.id:
        raise HTTPException(403, "Not your device")
    db.delete(device)
    db.commit()


# ── Send ─────────────────────────────────────────────────────────────────────

@router.post("/books/{book_id}/send")
def send_to_device(
    book_id: int,
    body: SendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, "member")

    if not settings.smtp_configured:
        raise HTTPException(400, "Email delivery is not configured. Ask your admin to set SMTP settings.")

    _check_daily_limit(db, user.id)

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book or not user_can_see_book(db, user, book):
        raise HTTPException(404, "Book not found")

    book_file = db.query(BookFile).filter(BookFile.id == body.file_id, BookFile.book_id == book_id).first()
    if not book_file:
        raise HTTPException(404, "File not found")

    device = db.query(UserDevice).filter(UserDevice.id == body.device_id).first()
    if not device or device.user_id != user.id:
        raise HTTPException(404, "Device not found")

    baked = get_baked_path(book, book_file)
    if not baked.exists():
        raise HTTPException(404, "Book file not found on disk")

    try:
        attachment_name = koreader_style_name(
            book.author, book.title, book.series_index, book_file.format
        )
        send_book_to_device(device.email, book.title, attachment_name, baked, book_file.format)
        status_str = "ok"
        error_str = None
    except FileTooLargeError as exc:
        status_str = "failed"
        error_str = str(exc)
        raise HTTPException(413, str(exc))
    except (SmtpNotConfiguredError, SmtpSendError) as exc:
        status_str = "failed"
        error_str = str(exc)
        raise HTTPException(502, str(exc))
    finally:
        audit(
            db,
            "books.sent_to_device",
            user_id=user.id,
            username=user.username,
            resource_type="book",
            resource_id=book.id,
            resource_title=book.title,
            details={
                "device_email": device.email,
                "device_name": device.name,
                "format": book_file.format,
                "status": status_str,
                "error": error_str,
            },
        )

    return {"ok": True}


@router.post("/send-to-device/bulk", response_model=BulkSendResponse)
def bulk_send_to_device(
    body: BulkSendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, "member")

    if not settings.smtp_configured:
        raise HTTPException(400, "Email delivery is not configured. Ask your admin to set SMTP settings.")

    if len(body.book_ids) > 25:
        raise HTTPException(400, "Maximum 25 books per bulk send")

    if not body.book_ids:
        raise HTTPException(400, "No books selected")

    _check_daily_limit(db, user.id)

    device = db.query(UserDevice).filter(UserDevice.id == body.device_id).first()
    if not device or device.user_id != user.id:
        raise HTTPException(404, "Device not found")

    to_send: list[tuple[str, str, Path, str, Book]] = []
    errors: list[dict] = []

    for bid in body.book_ids:
        book = db.query(Book).filter(Book.id == bid).first()
        if not book or not user_can_see_book(db, user, book):
            errors.append({"book_id": bid, "error": "Book not found"})
            continue
        bf = _get_best_file(book)
        if not bf:
            errors.append({"book_id": bid, "error": "No file available"})
            continue
        baked = get_baked_path(book, bf)
        if not baked.exists():
            errors.append({"book_id": bid, "error": "File not found on disk"})
            continue
        name = koreader_style_name(book.author, book.title, book.series_index, bf.format)
        to_send.append((book.title, name, baked, bf.format, book))

    sent = 0
    if to_send:
        results = send_books_bulk(
            device.email,
            [(title, name, path, fmt) for title, name, path, fmt, _ in to_send],
        )
        for (title, error), (_, _, _, fmt, book) in zip(results, to_send):
            status_str = "ok" if error is None else "failed"
            audit(
                db,
                "books.sent_to_device",
                user_id=user.id,
                username=user.username,
                resource_type="book",
                resource_id=book.id,
                resource_title=book.title,
                details={
                    "device_email": device.email,
                    "device_name": device.name,
                    "format": fmt,
                    "status": status_str,
                    "error": error,
                },
            )
            if error:
                errors.append({"book_id": book.id, "error": error})
            else:
                sent += 1

    return BulkSendResponse(sent=sent, failed=len(errors), errors=errors)


# ── SMTP status (any authenticated user) ─────────────────────────────────────

@router.get("/smtp-status", response_model=SmtpStatusPublic)
def smtp_status_public(user: User = Depends(get_current_user)):
    return SmtpStatusPublic(configured=settings.smtp_configured)


# ── Admin endpoints ──────────────────────────────────────────────────────────

@router.get("/admin/smtp-status", response_model=SmtpStatusAdmin)
def smtp_status_admin(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return SmtpStatusAdmin(
        configured=settings.smtp_configured,
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_address=settings.smtp_from_address or None,
    )


@router.post("/admin/smtp-test")
def smtp_test(
    body: SmtpTestRequest,
    user: User = Depends(get_current_user),
):
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    if not settings.smtp_configured:
        raise HTTPException(400, "SMTP is not configured")
    try:
        send_test_email(body.email)
        return {"ok": True}
    except (SmtpNotConfiguredError, SmtpSendError) as exc:
        raise HTTPException(502, str(exc))


@router.get("/admin/devices", response_model=list[AdminDeviceOut])
def admin_list_devices(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    rows = (
        db.query(UserDevice, User.username)
        .join(User, UserDevice.user_id == User.id)
        .order_by(UserDevice.created_at.desc())
        .all()
    )
    return [
        AdminDeviceOut(
            id=d.id,
            username=username,
            device_name=d.name,
            device_email=d.email,
            created_at=d.created_at,
        )
        for d, username in rows
    ]


@router.get("/admin/send-history", response_model=list[SendHistoryEntry])
def admin_send_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    entries = (
        db.query(AuditLog)
        .filter(AuditLog.action == "books.sent_to_device")
        .order_by(AuditLog.created_at.desc())
        .limit(100)
        .all()
    )
    result = []
    for e in entries:
        details = json.loads(e.details) if e.details else {}
        result.append(SendHistoryEntry(
            id=e.id,
            username=e.username,
            book_title=e.resource_title,
            device_email=details.get("device_email"),
            device_name=details.get("device_name"),
            status=details.get("status"),
            format=details.get("format"),
            created_at=e.created_at,
        ))
    return result
