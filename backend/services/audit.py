"""Audit logging helpers.

Usage:
    from backend.services.audit import audit
    audit(db, action="books.downloaded", user=current_user,
          resource_type="book", resource_id=book.id,
          resource_title=book.title, ip=ip_address)
"""
import json
from typing import Any

from sqlalchemy.orm import Session

from backend.models.audit_log import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    resource_title: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Write one audit log entry. Fire-and-forget — swallows exceptions so it
    never breaks a request."""
    try:
        entry = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_title=resource_title,
            details=json.dumps(details) if details else None,
            ip_address=ip,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
