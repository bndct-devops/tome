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


def excluded_tags_for(user: User) -> list[str]:
    """Tag names this user must never see (issue #190), lowercased.
    Stored as a JSON list in ``User.excluded_tags``; empty for admins and
    for users with no restriction set."""
    if is_admin(user) or not user.excluded_tags:
        return []
    import json
    try:
        raw = json.loads(user.excluded_tags)
    except (ValueError, TypeError):
        return []
    return [t.strip().lower() for t in raw if isinstance(t, str) and t.strip()]


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
    visible = or_(
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

    # Per-user content restriction (issue #190): a book carrying an excluded
    # tag is invisible — including the user's own uploads (the restriction is
    # account-wide by design, e.g. an under-18 account). Matching is
    # case-insensitive; note SQLite's lower() only folds ASCII, which is fine
    # for the tag conventions in use (CJK tags have no case).
    excluded = excluded_tags_for(user)
    if excluded:
        from sqlalchemy import func
        from backend.models.book import BookTag
        visible = and_(visible, ~Book.tags.any(func.lower(BookTag.tag).in_(excluded)))
    return visible


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


# ── Per-book gates for routes ────────────────────────────────────────────────
#
# Every route that takes a book id goes through one of these, so "may this
# user see it" and "may this user change it" are each decided in one place.
# tests/test_book_route_permissions.py walks every such route as a member
# against a book hidden from them.

def user_can_edit_book(user: User, book: "Book") -> bool:  # type: ignore[name-defined]
    """Admins change any book; everyone else only the books they uploaded."""
    return is_admin(user) or (book.added_by is not None and book.added_by == user.id)


def visible_book_or_404(db: Session, user: User, book_id: int, *, active_only: bool = False):
    """The book, or 404 when it does not exist or the user may not see it.
    A hidden book answers exactly like a missing one."""
    from backend.models.book import Book
    book = db.get(Book, book_id)
    if (
        book is None
        or (active_only and book.status != "active")
        or not user_can_see_book(db, user, book)
    ):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def editable_book_or_403(
    db: Session, user: User, book_id: int, *, detail: str = "You can only edit books you uploaded",
):
    """The book when the user may change it: 404 when hidden or missing, 403
    when visible but someone else's upload."""
    book = visible_book_or_404(db, user, book_id)
    if not user_can_edit_book(user, book):
        raise HTTPException(status_code=403, detail=detail)
    return book


def editable_books(db: Session, user: User, book_ids: list[int]) -> tuple[list, list[int]]:
    """For bulk routes: (books the user may change, ids left out). Ids that
    are missing, hidden or someone else's upload are left out, not an error,
    so one foreign book does not fail a whole selection."""
    from backend.models.book import Book
    wanted = list(dict.fromkeys(book_ids))
    if not wanted:
        return [], []
    query = db.query(Book).filter(Book.id.in_(wanted), book_visibility_filter(db, user))
    if not is_admin(user):
        query = query.filter(Book.added_by == user.id)
    books = query.all()
    allowed = {b.id for b in books}
    return books, [i for i in wanted if i not in allowed]
