"""The reading companion's switch (``ai_companion_optin``).

``enabled`` gates both automatic triggers in ``digest.py``; ``floor`` is the
chapter automatic reads of a book never go back before. Explicit starts
(``POST .../digest``, Read again, Write all) ignore both: the user asked.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_companion_optin import AICompanionOptIn
from backend.models.book import Book
from backend.models.user import User

# Tests only: what ``enabled`` answers when no row exists (None = off, the
# production default). The suite's trigger tests run with it on.
_TEST_DEFAULT: bool | None = None


def scope_key(book: Book) -> str:
    from backend.services.ai.digest import series_key
    return series_key(book)


def scope_kind(book: Book) -> str:
    return "series" if book.series else "book"


def get(db: Session, user: User, book: Book) -> AICompanionOptIn | None:
    return (db.query(AICompanionOptIn)
            .filter(AICompanionOptIn.user_id == user.id, AICompanionOptIn.scope_key == scope_key(book))
            .first())


def enabled(db: Session, user: User, book: Book) -> bool:
    row = get(db, user, book)
    if row is None:
        return bool(_TEST_DEFAULT) if _TEST_DEFAULT is not None else False
    return row.enabled


def floor(db: Session, user: User, book: Book) -> int:
    """The first chapter of ``book`` an automatic read may cover: the chapter
    the user was in when they switched the companion on in this book, 0 for
    every other volume of the series."""
    row = get(db, user, book)
    if row is None or not row.enabled or row.start_book_id != book.id:
        return 0
    return row.start_chapter_idx


def set_enabled(db: Session, user: User, book: Book, on: bool, position_chapter: int) -> AICompanionOptIn:
    """Switch the companion on or off for ``book``'s series (or the book).
    Switching on records the start: chapter 0 when the book already has
    digests (the read continues where it stopped), else the reader's current
    chapter, so nothing before it is read without being asked."""
    row = get(db, user, book)
    if row is None:
        row = AICompanionOptIn(user_id=user.id, scope_key=scope_key(book))
        db.add(row)
    row.enabled = on
    if on:
        has_digests = db.query(AIChapterDigest.id).filter(AIChapterDigest.book_id == book.id).first() is not None
        row.start_book_id = book.id
        row.start_chapter_idx = 0 if has_digests else max(0, int(position_chapter))
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def payload(db: Session, user: User, book: Book) -> dict[str, Any]:
    row = get(db, user, book)
    on = enabled(db, user, book)
    return {
        "enabled": on,
        "scope": scope_kind(book),
        "start": ({"book_id": row.start_book_id, "chapter_idx": row.start_chapter_idx}
                  if row is not None and on and row.start_book_id is not None else None),
    }
