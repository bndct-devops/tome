"""Shared book-edit code paths.

``PUT /books/{id}`` and ``PUT /books/bulk-metadata`` call these, and so does
any feature that writes the same fields (the AI series cleanup apply), so the
edit rules live in one place. Nothing here commits: callers own the
transaction and the audit entry.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from backend.core.permissions import is_admin
from backend.models.book import Book, BookTag
from backend.models.user import User


def isbn_allowed(isbn: Optional[str], edition_format: Optional[str] = None) -> bool:
    """May this ISBN be written to a Tome book? Tome has no audio format, so
    an ISBN known to be an audiobook edition never is: known from the
    candidate that supplied it (``edition_format == "audiobook"``) or from an
    audiobook edition Hardcover listed earlier in this process
    (``hardcover_editions.is_known_audio``). No network call."""
    if not isbn:
        return True
    if (edition_format or "").strip().lower() == "audiobook":
        return False
    from backend.services.hardcover_editions import is_known_audio
    return not is_known_audio(isbn)


def _set_isbn(book: Book, isbn: Optional[str], keep_hardcover_match: bool) -> None:
    if keep_hardcover_match:
        book._keep_hardcover_match = True  # read by the Book.isbn listener
        try:
            book.isbn = isbn
        finally:
            book._keep_hardcover_match = False
    else:
        book.isbn = isbn


def write_isbn(book: Book, isbn: Optional[str], edition_format: Optional[str] = None,
               *, keep_hardcover_match: bool = False) -> bool:
    """Set ``book.isbn`` unless ``isbn_allowed`` refuses it. Returns whether
    it was written. Changing the ISBN clears an automatic Hardcover match
    (see the ``Book.isbn`` listener in models/book.py) unless
    ``keep_hardcover_match``: the Hardcover sync correcting the ISBN of the
    book it has matched keeps the match it is syncing."""
    if not isbn or not isbn_allowed(isbn, edition_format):
        return False
    _set_isbn(book, isbn, keep_hardcover_match)
    return True


def apply_isbn_fix(book: Book, fix, *, keep_hardcover_match: bool = False) -> Optional[dict]:
    """Write a ``hardcover_editions.IsbnFix`` (from ``decide_isbn``) through
    the guard: the new ISBN, or none when the fix removes a known audiobook
    ISBN; the edition's publisher only when the book has none. Returns
    ``{book_id, title, from, to, reason}``, or None when nothing changed."""
    from backend.services.hardcover_editions import edition_format, edition_publisher

    old = book.isbn
    if fix.isbn is None:
        _set_isbn(book, None, keep_hardcover_match)
    elif not write_isbn(book, fix.isbn, edition_format(fix.edition or {}),
                        keep_hardcover_match=keep_hardcover_match):
        return None
    if fix.edition and not book.publisher:
        book.publisher = edition_publisher(fix.edition) or book.publisher
    return {"book_id": book.id, "title": book.title, "from": old, "to": book.isbn,
            "reason": fix.reason}


class BookUpdateError(ValueError):
    """A requested edit the API should answer with 400."""


def can_edit_book(user: User, book: Book) -> bool:
    """Members edit only their own uploads; admins edit any book."""
    return is_admin(user) or book.added_by == user.id


def apply_book_update(db: Session, book: Book, data: dict[str, Any]) -> None:
    """Apply a ``BookUpdate``-shaped dict (``model_dump(exclude_unset=True)``)
    to one book, then flush and re-index it. Raises ``BookUpdateError`` for an
    unknown ``book_type_id``. Does not commit."""
    data = dict(data)
    tags = data.pop("tags", None)
    new_type_id = data.pop("book_type_id", None)
    for field_name, value in data.items():
        setattr(book, field_name, value)
    if tags is not None:
        book.tags = [BookTag(book_id=book.id, tag=t.strip(), source="user") for t in tags if t.strip()]

    if new_type_id is not None and new_type_id != book.book_type_id:
        from backend.models.library import BookType
        from backend.services.book_types import assign_book_to_type_library
        bt = db.get(BookType, new_type_id)
        if not bt:
            raise BookUpdateError("Invalid book_type_id")
        book.book_type_id = new_type_id
        assign_book_to_type_library(db, book, bt)

    db.flush()
    from backend.services.fts import index_book
    index_book(db, book)


def bulk_update_books(
    db: Session,
    books: Iterable[Book],
    *,
    author: Optional[str] = None,
    series: Optional[str] = None,
    series_index: Optional[float] = None,
    tags: Optional[list[str]] = None,
    tags_add: Optional[list[str]] = None,
    book_type_id: Optional[int] = None,
) -> None:
    """The bulk-metadata edit: set the given fields on every book (``""``
    clears author / series), then flush and re-index. ``book_type_id`` only
    sets the column; the caller assigns the type library after committing,
    as the bulk endpoint always has. Does not commit."""
    books = list(books)
    for book in books:
        if author is not None:
            book.author = author or None
        if series is not None:
            book.series = series or None
        if series_index is not None:
            book.series_index = series_index
        if tags is not None:
            book.tags = [BookTag(book_id=book.id, tag=t.strip(), source="user") for t in tags if t.strip()]
        elif tags_add:
            existing = {t.tag for t in book.tags}
            for t in tags_add:
                if t.strip() and t.strip() not in existing:
                    book.tags.append(BookTag(book_id=book.id, tag=t.strip(), source="user"))
        if book_type_id is not None:
            book.book_type_id = book_type_id
    db.flush()
    from backend.services.fts import index_book
    for book in books:
        index_book(db, book)
