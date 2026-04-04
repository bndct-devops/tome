"""
Seed default book types and handle auto-library assignment.
"""
from sqlalchemy.orm import Session

from backend.models.library import BookType, Library

DEFAULT_TYPES = [
    {"slug": "book",        "label": "Books",        "icon": "BookOpen",   "color": "blue",   "sort_order": 0},
    {"slug": "manga",       "label": "Manga",        "icon": "BookMarked", "color": "pink",   "sort_order": 1},
    {"slug": "comic",       "label": "Comics",       "icon": "Layers",     "color": "orange", "sort_order": 2},
    {"slug": "light_novel", "label": "Light Novels", "icon": "Scroll",     "color": "purple", "sort_order": 3},
]


def seed_book_types(db: Session) -> None:
    """Create default book types if none exist."""
    if db.query(BookType).count() == 0:
        for t in DEFAULT_TYPES:
            db.add(BookType(**t))
        db.commit()


def get_or_create_type_library(db: Session, book_type: BookType) -> Library:
    """Return (and create if needed) the library associated with a book type."""
    if book_type.library_id:
        lib = db.get(Library, book_type.library_id)
        if lib:
            return lib

    lib = Library(
        name=book_type.label,
        icon=book_type.icon,
        is_public=True,
        sort_order=book_type.sort_order,
    )
    db.add(lib)
    db.flush()  # get lib.id before commit

    book_type.library_id = lib.id
    db.commit()
    db.refresh(lib)
    return lib


def assign_book_to_type_library(db: Session, book, book_type: BookType) -> None:
    """Ensure the book is in its type library (add if missing)."""
    lib = get_or_create_type_library(db, book_type)
    if lib not in book.libraries:
        book.libraries.append(lib)
    db.commit()
