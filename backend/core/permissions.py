from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_
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
    the user is allowed to see. Kept in sync with the visibility logic in
    GET /api/books. If you change one, change the other.
    """
    from backend.models.book import Book
    from backend.models.library import Library, library_users_table
    from backend.models.user import User as _User

    if is_admin(user):
        return True

    admin_ids = [
        u.id for u in db.query(_User).filter(
            (_User.is_admin == True) | (_User.role == "admin")  # noqa: E712
        ).all()
    ]

    if user.role == "member":
        assigned_lib_ids = [
            row[1] for row in db.execute(
                library_users_table.select().where(
                    library_users_table.c.user_id == user.id
                )
            ).fetchall()
        ]
        conditions = [
            Book.added_by.in_(admin_ids),
            Book.added_by.is_(None),
            Book.added_by == user.id,
        ]
        if assigned_lib_ids:
            conditions.append(Book.libraries.any(Library.id.in_(assigned_lib_ids)))
        return or_(*conditions)

    return or_(
        Book.added_by.in_(admin_ids),
        Book.added_by.is_(None),
        Book.libraries.any(Library.is_public == True),  # noqa: E712
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
