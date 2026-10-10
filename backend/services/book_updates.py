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
