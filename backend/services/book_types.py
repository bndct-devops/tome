"""
Seed default book types and handle auto-library assignment.
"""
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.library import BookType, Library
from backend.services.layout import DEFAULT_PRESET, TypeLayout

# Every default type starts without a folder and with the series_first
# layout: the layout of an existing library must not change on upgrade
# (#270, #276).
DEFAULT_TYPES = [
    {"slug": "book",        "label": "Books",        "icon": "BookOpen",   "color": "blue",   "sort_order": 0, "folder": ""},
    {"slug": "manga",       "label": "Manga",        "icon": "BookMarked", "color": "pink",   "sort_order": 1, "folder": ""},
    {"slug": "comic",       "label": "Comics",       "icon": "Layers",     "color": "orange", "sort_order": 2, "folder": ""},
    {"slug": "light_novel", "label": "Light Novels", "icon": "Scroll",     "color": "purple", "sort_order": 3, "folder": ""},
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


# ── Type folders (#270) ───────────────────────────────────────────────────────

FOLDER_MAX_LEN = 64


def normalize_folder(raw: Optional[str]) -> str:
    """Validate and normalise a type folder name. Returns "" for none.

    One plain path component only: no separators, no ``.``/``..``, no leading
    dot (hidden folders are skipped by the bindery and by most tools).
    Raises ValueError with a user-facing message when invalid.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "/" in s or "\\" in s:
        raise ValueError("Folder must be a single name, without / or \\")
    if s in (".", "..") or s.startswith("."):
        raise ValueError("Folder must not start with a dot")
    if len(s) > FOLDER_MAX_LEN:
        raise ValueError(f"Folder must be at most {FOLDER_MAX_LEN} characters")
    if any(ord(c) < 32 for c in s):
        raise ValueError("Folder contains control characters")
    return s


def type_folder(book_type: Optional[BookType]) -> str:
    """The folder a book of this type is stored under ("" for none)."""
    return (book_type.folder or "") if book_type is not None else ""


def folder_for_type_id(db: Session, book_type_id: Optional[int]) -> str:
    if not book_type_id:
        return ""
    return type_folder(db.get(BookType, book_type_id))


def type_layout(book_type: Optional[BookType]) -> TypeLayout:
    """Folder, layout preset and template of a type (#276). No type, or a
    type left at the defaults, is the original layout."""
    if book_type is None:
        return TypeLayout()
    return TypeLayout(
        folder=book_type.folder or "",
        preset=book_type.layout_preset or DEFAULT_PRESET,
        template=book_type.layout_template or "",
    )


def layout_for_type_id(db: Session, book_type_id: Optional[int]) -> TypeLayout:
    if not book_type_id:
        return TypeLayout()
    return type_layout(db.get(BookType, book_type_id))


def detect_book_type(
    db: Session,
    *,
    fmt: Optional[str],
    is_manga: bool = False,
    in_chapters_dir: bool = False,
) -> Optional[BookType]:
    """The type an incoming file gets automatically, before it is placed.

    Same rule the importers have always used: a chapters/ drop or a
    ComicInfo manga flag → manga, a cbz/cbr → comic, anything else none.
    """
    slug: Optional[str] = None
    if in_chapters_dir or is_manga:
        slug = "manga"
    elif (fmt or "").lower() in ("cbz", "cbr"):
        slug = "comic"
    if slug is None:
        return None
    return db.query(BookType).filter(BookType.slug == slug).first()


def type_for_top_folder(db: Session, top: Optional[str]) -> Optional[BookType]:
    """Scan hint: the type whose folder (or label) matches a library top-level
    folder, case-insensitively. A folder match beats a label match."""
    if not top:
        return None
    key = top.strip().lower()
    if not key:
        return None
    types = db.query(BookType).order_by(BookType.sort_order, BookType.id).all()
    for bt in types:
        if (bt.folder or "").lower() == key:
            return bt
    for bt in types:
        if (bt.label or "").strip().lower() == key:
            return bt
    return None
