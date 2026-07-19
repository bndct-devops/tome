"""Resolve a shelf's saved filter params to a Book query.

Shared by the KOReader device browser (build 36) and public share links. The
device-supported subset of the dashboard's filters; the expressions mirror
backend.api.books.list_books — that endpoint is the source of these
semantics (test_tomesync_shelves has a drift check against /books).
"""
from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from backend.core.permissions import book_visibility_filter
from backend.models.book import Book, BookFile, BookTag
from backend.models.library import Library
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus

SHELF_SUPPORTED = {"q", "series", "no_series", "author", "tag", "format",
                   "language", "library_id", "reading_status", "min_rating"}


def shelf_query(db: Session, user: User, params: dict):
    """-> (query over Book, unsupported_keys). Values arrive as URL-shaped
    strings ('true', '3'); coerce where needed. Visibility is always the
    given user's — a shelf can never show more than its owner can see."""
    from sqlalchemy import text as sa_text

    visibility = book_visibility_filter(db, user)
    query = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.book_type))
        .filter(Book.status == "active", visibility)
    )
    unsupported = sorted(k for k, v in params.items()
                         if k not in SHELF_SUPPORTED and v not in (None, "", False))

    q = params.get("q")
    if q:
        terms = str(q).split()
        fts_term = " ".join(f'"{t.replace(chr(34), "")}"*' for t in terms if t)
        fts_ids = [r[0] for r in db.execute(
            sa_text("SELECT rowid FROM books_fts WHERE books_fts MATCH :q"),
            {"q": fts_term}).fetchall()]
        query = query.filter(Book.id.in_(fts_ids) if fts_ids else Book.id == -1)
    if params.get("series"):
        query = query.filter(Book.series == params["series"])
    if str(params.get("no_series")).lower() == "true":
        query = query.filter(Book.series.is_(None))
    if params.get("author"):
        query = query.filter(Book.author == params["author"])
    if params.get("tag"):
        query = query.join(Book.tags).filter(BookTag.tag == params["tag"])
    if params.get("format"):
        query = query.join(Book.files).filter(BookFile.format == str(params["format"]).lower())
    if params.get("language"):
        from backend.services.languages import normalize_language
        target = normalize_language(str(params["language"]))
        raw = [r[0] for r in db.query(Book.language).filter(
            Book.language.isnot(None), Book.language != "").distinct().all()
            if normalize_language(r[0]) == target]
        query = query.filter(Book.language.in_(raw) if raw else Book.id == -1)
    if params.get("library_id"):
        try:
            query = query.join(Book.libraries).filter(Library.id == int(params["library_id"]))
        except (TypeError, ValueError):
            pass
    rs = params.get("reading_status")
    if rs in ("reading", "read", "shelved"):
        query = query.join(
            UserBookStatus,
            (UserBookStatus.book_id == Book.id) & (UserBookStatus.user_id == user.id)
        ).filter(UserBookStatus.status == rs)
    elif rs == "unread":
        from sqlalchemy import exists
        subq = exists().where(
            (UserBookStatus.book_id == Book.id) &
            (UserBookStatus.user_id == user.id) &
            (UserBookStatus.status != "unread")
        )
        query = query.filter(~subq)
    if params.get("min_rating"):
        try:
            query = query.join(
                UserBookStatus,
                (UserBookStatus.book_id == Book.id) & (UserBookStatus.user_id == user.id)
            ).filter(UserBookStatus.rating >= float(params["min_rating"]))
        except (TypeError, ValueError):
            pass
    return query, unsupported
