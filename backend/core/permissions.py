from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
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


def book_visibility_filter(db: Session, user: User):
    """Return a SQLAlchemy filter expression restricting Book rows to those
    the user is allowed to see. This is the single source of truth for book
    visibility — the books, downloads, stats, OPDS and TomeSync surfaces all
    route through it (directly or via :func:`user_can_see_book`).

    Library membership is the gate: a book placed in a private library is
    visible only to the library owner, its assigned users, and admins. A book
    that is in *no* library at all falls back to the legacy "shared
    collection" rule — admin-uploaded (or uploader-less, legacy) unfiled books
    stay visible to everyone, while a member's own unfiled upload stays private
    to just them. Admins always see everything.
    """
    from backend.models.book import Book
    from backend.models.library import Library
    from backend.models.user import User as _User

    if is_admin(user):
        return True

    admin_ids = [
        u.id for u in db.query(_User).filter(
            (_User.is_admin == True) | (_User.role == "admin")  # noqa: E712
        ).all()
    ]

    no_library = ~Book.libraries.any()
    return or_(
        # Own uploads are always visible to their uploader.
        Book.added_by == user.id,
        # Unfiled books fall back to the shared-collection rule: admin-uploaded
        # or legacy (no uploader) books stay public; a member's own unfiled
        # upload is already covered by the clause above and stays private.
        and_(
            no_library,
            or_(Book.added_by.is_(None), Book.added_by.in_(admin_ids)),
        ),
        # Library membership gates everything else.
        Book.libraries.any(Library.is_public == True),  # noqa: E712
        Book.libraries.any(Library.owner_id == user.id),
        Book.libraries.any(Library.assigned_users.any(_User.id == user.id)),
    )


def user_can_see_book(db: Session, user: User, book: "Book") -> bool:  # type: ignore[name-defined]
    """Single-book visibility check using the same rules as book_visibility_filter."""
    from backend.models.book import Book
    if is_admin(user):
        return True
    exists = db.query(Book.id).filter(
        Book.id == book.id,
        book_visibility_filter(db, user),
    ).first()
    return exists is not None
