import csv
import difflib
import io
import re
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, File, Form, status
from backend.services.safe_fetch import fetch_safe_image, UnsafeURLError
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role
from backend.models.book import Book, BookFile, BookTag
from backend.models.user_book_status import UserBookStatus
from backend.services.audit import audit
from backend.models.library import Library
from backend.models.user import User
from pydantic import BaseModel as PydanticBaseModel
from backend.schemas.book import (
    ApplyMetadataRequest, BookDetailOut, BookOut, BookUpdate,
    MetadataCandidateOut, ScanResultOut,
)
from backend.services.scanner import import_incoming, scan_library
from backend.services.metadata import extract_metadata, get_format, sha256_file
from backend.services.metadata_fetch import fetch_candidates
from backend.services.organizer import get_library_path, resolve_unique_path

router = APIRouter(prefix="/books", tags=["books"])
logger = logging.getLogger(__name__)

ALLOWED_FORMATS = {"epub", "pdf", "cbz", "cbr", "mobi"}


# ── Import incoming ───────────────────────────────────────────────────────────

class ScanOptions(PydanticBaseModel):
    default_type_id: int | None = None

@router.post("/import", response_model=ScanResultOut)
def trigger_import(
    opts: ScanOptions = ScanOptions(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import new files from incoming/ into the library."""
    require_role(current_user, "member")

    result = import_incoming(
        incoming_dir=settings.incoming_dir,
        library_dir=settings.library_dir,
        covers_dir=settings.covers_dir,
        db=db,
        added_by=current_user.id,
    )
    _apply_default_type(db, result.added_ids, opts.default_type_id)
    return result


@router.post("/scan", response_model=ScanResultOut)
def trigger_scan(
    opts: ScanOptions = ScanOptions(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scan library/ for files not yet in the DB (e.g. manually added)."""
    require_role(current_user, "member")

    result = scan_library(
        library_dir=settings.library_dir,
        covers_dir=settings.covers_dir,
        db=db,
        added_by=current_user.id,
    )
    _apply_default_type(db, result.added_ids, opts.default_type_id)
    return result


def _apply_default_type(db: Session, book_ids: list[int], type_id: int | None) -> None:
    if not type_id or not book_ids:
        return
    from backend.models.library import BookType
    from backend.services.book_types import assign_book_to_type_library
    bt = db.get(BookType, type_id)
    if not bt:
        return
    books = db.query(Book).filter(Book.id.in_(book_ids)).all()
    for book in books:
        book.book_type_id = type_id
        assign_book_to_type_library(db, book, bt)


# ── List ──────────────────────────────────────────────────────────────────────

SORT_FIELDS = {
    "title": Book.title,
    "author": Book.author,
    "year": Book.year,
    "added_at": Book.added_at,
}

@router.get("", response_model=list[BookOut])
def list_books(
    response: Response,
    q: Optional[str] = Query(None, description="Full-text search across title/author/series/tags"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    sort: str = Query("title", pattern="^(title|author|year|added_at)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    series: Optional[str] = Query(None, description="Exact series name filter"),
    no_series: Optional[bool] = Query(None, description="Filter to books with no series"),
    author: Optional[str] = Query(None, description="Exact author filter"),
    tag: Optional[str] = Query(None, description="Tag name filter"),
    format: Optional[str] = Query(None, description="File format filter (epub, pdf, …)"),
    library_id: Optional[int] = Query(None, description="Filter to books in this library"),
    reading_status: Optional[str] = Query(None, description="Filter by reading status: unread, reading, read"),
    missing: Optional[str] = Query(None, description="Filter books missing a field: cover, description, author, series, any"),
    content_type: Optional[str] = Query(None, description="Filter by content type: volume, chapter"),
    added_by: Optional[int] = Query(None, description="Filter by uploader user ID (admin only)"),
    ownership: Optional[str] = Query(None, description="Ownership filter: 'mine' or 'shared' (member only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.core.permissions import is_admin as _is_admin
    from sqlalchemy import or_

    query = db.query(Book).filter(Book.status == "active")

    # ── Role-based visibility ────────────────────────────────────────────────
    if not _is_admin(current_user):
        from backend.models.library import library_users_table
        # Collect admin user IDs (NULL added_by also treated as admin-uploaded)
        admin_ids = [
            u.id for u in db.query(User).filter(
                (User.is_admin == True) | (User.role == "admin")
            ).all()
        ]
        if current_user.role == "member":
            # Member sees: admin-uploaded books + own uploads + books in assigned libraries
            assigned_lib_ids = [
                row[1] for row in db.execute(
                    library_users_table.select().where(
                        library_users_table.c.user_id == current_user.id
                    )
                ).fetchall()
            ]
            visibility_conditions = [
                Book.added_by.in_(admin_ids),
                Book.added_by.is_(None),        # legacy books without uploader = shared
                Book.added_by == current_user.id,
            ]
            if assigned_lib_ids:
                visibility_conditions.append(
                    Book.libraries.any(Library.id.in_(assigned_lib_ids))
                )
            query = query.filter(or_(*visibility_conditions))
        else:
            # Guest sees: admin-uploaded books + books in public libraries
            query = query.filter(
                or_(
                    Book.added_by.in_(admin_ids),
                    Book.added_by.is_(None),    # legacy books without uploader = shared
                    Book.libraries.any(Library.is_public == True),
                )
            )

    if q:
        from sqlalchemy import text as sa_text
        # Split into individual prefix-matched terms (AND semantics is FTS5 default)
        terms = q.split()
        fts_term = " ".join(f'"{t.replace(chr(34), "")}"*' for t in terms if t)
        fts_rows = db.execute(
            sa_text("SELECT rowid FROM books_fts WHERE books_fts MATCH :q ORDER BY rank"),
            {"q": fts_term},
        ).fetchall()
        fts_ids = [row[0] for row in fts_rows]
        if fts_ids:
            query = query.filter(Book.id.in_(fts_ids))
        else:
            query = query.filter(Book.id == -1)
    if series:
        query = query.filter(Book.series == series)
    if no_series:
        query = query.filter(Book.series.is_(None))
    if author:
        query = query.filter(Book.author == author)
    if tag:
        query = query.join(Book.tags).filter(BookTag.tag == tag)
    if format:
        query = query.join(Book.files).filter(BookFile.format == format.lower())
    if library_id:
        query = query.join(Book.libraries).filter(Library.id == library_id)
    if reading_status in ("reading", "read"):
        query = query.join(
            UserBookStatus,
            (UserBookStatus.book_id == Book.id) & (UserBookStatus.user_id == current_user.id)
        ).filter(UserBookStatus.status == reading_status)
    elif reading_status == "unread":
        # Unread = no status row, or status explicitly "unread"
        from sqlalchemy import or_, not_, exists
        subq = exists().where(
            (UserBookStatus.book_id == Book.id) &
            (UserBookStatus.user_id == current_user.id) &
            (UserBookStatus.status != "unread")
        )
        query = query.filter(~subq)

    if missing:
        from sqlalchemy import or_ as sa_or_
        no_cover = Book.cover_path.is_(None)
        no_description = sa_or_(Book.description.is_(None), Book.description == "")
        no_author = sa_or_(Book.author.is_(None), Book.author == "")
        no_series = sa_or_(Book.series.is_(None), Book.series == "")
        if missing == "cover":
            query = query.filter(no_cover)
        elif missing == "description":
            query = query.filter(no_description)
        elif missing == "author":
            query = query.filter(no_author)
        elif missing == "series":
            query = query.filter(no_series)
        elif missing == "any":
            query = query.filter(sa_or_(no_cover, no_description, no_author, no_series))

    if content_type:
        query = query.filter(Book.content_type == content_type)

    # ── Ownership / uploader filters ─────────────────────────────────────────
    from backend.core.permissions import is_admin as _is_admin_check
    if added_by is not None and _is_admin_check(current_user):
        query = query.filter(Book.added_by == added_by)
    if ownership == "mine":
        query = query.filter(Book.added_by == current_user.id)
    elif ownership == "shared":
        from sqlalchemy import or_ as _or_
        shared_admin_ids = [
            u.id for u in db.query(User).filter(
                (User.is_admin == True) | (User.role == "admin")
            ).all()
        ]
        query = query.filter(
            _or_(
                Book.added_by.in_(shared_admin_ids),
                Book.added_by.is_(None),
            )
        )

    # When filtering by a specific series, always sort by series_index
    if series:
        query = query.order_by(Book.series_index.asc().nullslast(), Book.title.asc())
    elif sort == "status_updated" and reading_status:
        # Sort by when the reading status was last updated (needs UserBookStatus join)
        if reading_status not in ("reading", "read"):
            # Fallback — status_updated only meaningful with a status filter
            query = query.order_by(Book.added_at.desc(), Book.title.asc())
        else:
            # UserBookStatus already joined above for reading_status filter
            status_sort = UserBookStatus.updated_at.desc() if order == "desc" else UserBookStatus.updated_at.asc()
            query = query.order_by(status_sort, Book.title.asc())
    else:
        sort_col = SORT_FIELDS.get(sort, Book.title)
        sort_expr = sort_col.asc() if order == "asc" else sort_col.desc()
        # Always secondary-sort by title so results are stable
        if sort != "title":
            query = query.order_by(sort_expr, Book.title.asc())
        else:
            query = query.order_by(sort_expr)

    total = query.distinct().count()
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return query.distinct().offset(skip).limit(limit).all()


# ── Facets (for filter dropdowns) ────────────────────────────────────────────

@router.get("/facets", response_model=dict)
def get_facets(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return distinct values for filter dropdowns: series, authors, tags, formats."""
    from sqlalchemy import func

    series = [r[0] for r in db.query(Book.series).filter(
        Book.status == "active", Book.series.isnot(None)
    ).distinct().order_by(Book.series).all()]

    authors = [r[0] for r in db.query(Book.author).filter(
        Book.status == "active", Book.author.isnot(None)
    ).distinct().order_by(Book.author).all()]

    tags = [r[0] for r in db.query(BookTag.tag).join(Book).filter(
        Book.status == "active"
    ).distinct().order_by(BookTag.tag).all()]

    formats = [r[0] for r in db.query(BookFile.format).join(Book).filter(
        Book.status == "active"
    ).distinct().order_by(BookFile.format).all()]

    return {"series": series, "authors": authors, "tags": tags, "formats": formats}


# ── Series list ───────────────────────────────────────────────────────────────

@router.get("/series")
def get_series(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all series with book count, cover book id, description snippet, and reading status counts."""
    from sqlalchemy import func

    series_rows = (
        db.query(Book.series, func.count(Book.id).label("book_count"))
        .filter(Book.status == "active", Book.series.isnot(None))
        .group_by(Book.series)
        .order_by(Book.series)
        .all()
    )

    # Fetch all reading statuses for this user in one query — keyed by book_id
    all_statuses: dict[int, str] = {}
    status_rows = (
        db.query(UserBookStatus.book_id, UserBookStatus.status)
        .filter(UserBookStatus.user_id == current_user.id)
        .all()
    )
    for book_id, st in status_rows:
        all_statuses[book_id] = st

    result = []
    for series_name, book_count in series_rows:
        # Pick the book with the lowest series_index as the cover; fall back to lowest id if all null
        series_books = (
            db.query(Book)
            .filter(Book.status == "active", Book.series == series_name)
            .order_by(
                Book.series_index.is_(None),  # nulls last
                Book.series_index,
                Book.id,
            )
            .all()
        )
        first_book = series_books[0] if series_books else None

        read_count = sum(1 for b in series_books if all_statuses.get(b.id) == "read")
        reading_count = sum(1 for b in series_books if all_statuses.get(b.id) == "reading")

        result.append({
            "name": series_name,
            "book_count": book_count,
            "cover_book_id": first_book.id if first_book else None,
            "description": first_book.description if first_book else None,
            "author": first_book.author if first_book else None,
            "read_count": read_count,
            "reading_count": reading_count,
        })

    # Append unserialized bucket if any books have no series
    unserialized_count = (
        db.query(func.count(Book.id))
        .filter(Book.status == "active", Book.series.is_(None))
        .scalar()
    )
    if unserialized_count:
        first_unserialized = (
            db.query(Book)
            .filter(Book.status == "active", Book.series.is_(None))
            .order_by(Book.id)
            .first()
        )
        result.append({
            "name": "__unserialized__",
            "book_count": unserialized_count,
            "cover_book_id": first_unserialized.id if first_unserialized else None,
            "description": None,
            "author": None,
            "read_count": 0,
            "reading_count": 0,
        })

    return result


# ── Series detail ─────────────────────────────────────────────────────────────

@router.get("/series-detail")
def get_series_detail(
    name: str = Query(..., description="Series name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all books in a series with per-user reading status, ordered by series_index."""
    books = (
        db.query(Book)
        .filter(Book.status == "active", Book.series == name)
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )

    # Fetch reading statuses for all books in one query
    book_ids = [b.id for b in books]
    statuses: dict[int, UserBookStatus] = {}
    if book_ids:
        rows = (
            db.query(UserBookStatus)
            .filter(
                UserBookStatus.user_id == current_user.id,
                UserBookStatus.book_id.in_(book_ids),
            )
            .all()
        )
        statuses = {r.book_id: r for r in rows}

    # Build author and description from first book
    first = books[0] if books else None
    author = first.author if first else None
    description = first.description if first else None

    book_list = []
    for b in books:
        st = statuses.get(b.id)
        book_list.append({
            "id": b.id,
            "title": b.title,
            "series_index": b.series_index,
            "cover_path": b.cover_path,
            "reading_status": st.status if st else "unread",
            "progress_pct": st.progress_pct if st else None,
        })

    return {
        "name": name,
        "author": author,
        "description": description,
        "books": book_list,
    }


@router.get("/export")
def export_books(
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    books = db.query(Book).filter(Book.status == "active").all()
    rows = []
    for b in books:
        rows.append({
            "id": b.id,
            "title": b.title,
            "author": b.author or "",
            "series": b.series or "",
            "series_index": b.series_index,
            "year": b.year,
            "language": b.language or "",
            "publisher": b.publisher or "",
            "isbn": b.isbn or "",
            "tags": ", ".join(t.tag for t in b.tags),
            "formats": ", ".join(f.format for f in b.files),
            "added_at": b.added_at.isoformat(),
        })

    if format == "csv":
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="tome-books.csv"'},
        )
    else:
        return StreamingResponse(
            iter([json.dumps(rows, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="tome-books.json"'},
        )


# ── Standardize titles ───────────────────────────────────────────────────────

import re as _re

_VOLUME_PATTERN = _re.compile(
    r'[\s,]*'
    r'(?:[-\u2013]\s*)?'
    r'(?:'
    r'\(?[Vv]ol(?:ume)?\.?\s*(\d+(?:\.\d+)?)\)?'
    r'|[Vv](\d+(?:\.\d+)?)'
    r'|#(\d+(?:\.\d+)?)'
    r')'
    r'\s*$'
)

_SUBTITLE_SEP = _re.compile(r'\s*[-\u2013:]\s+')


def _standardize_book_title(book: Book) -> dict:
    title = book.title or ""
    series = book.series or ""
    series_index = book.series_index

    proposed_title = title
    proposed_subtitle: str | None = book.subtitle
    proposed_series: str | None = series or None
    proposed_series_index: float | None = series_index

    if series:
        # Case 1: book has series — title should be the series name
        vol_match = _VOLUME_PATTERN.search(title)

        # Only strip volume suffix (and trailing noise) when a volume pattern was found
        if vol_match:
            stripped = _VOLUME_PATTERN.sub("", title).strip().rstrip(",-: ")
            # Extract volume number if not already set
            if series_index is None:
                raw = next(g for g in vol_match.groups() if g is not None)
                try:
                    proposed_series_index = float(raw) if "." in raw else int(raw)
                except ValueError:
                    pass
        else:
            stripped = title

        # Compare stripped title to series name (case-insensitive prefix)
        series_lower = series.strip().lower()
        stripped_lower = stripped.lower()

        if stripped_lower == series_lower:
            # Title is exactly the series name — nothing left for subtitle
            proposed_title = series
            proposed_subtitle = None
        elif stripped_lower.startswith(series_lower):
            # Extra text after the series name → subtitle
            extra = stripped[len(series):].strip()
            # Strip any leading separator characters (dash, colon, comma, space)
            extra = _re.sub(r'^[-\u2013:,\s]+', '', extra).strip()
            proposed_title = series
            proposed_subtitle = extra if extra else None
        elif vol_match:
            # Volume was stripped but title doesn't start with series name — just use stripped title
            proposed_title = stripped if stripped else title
        # else: no volume match, title doesn't resemble series — no change

    else:
        # Case 2: no series, but title contains a volume pattern
        vol_match = _VOLUME_PATTERN.search(title)
        if vol_match:
            raw = next(g for g in vol_match.groups() if g is not None)
            stripped = _VOLUME_PATTERN.sub("", title).strip().rstrip(",-: ")
            try:
                proposed_series_index = float(raw) if "." in raw else int(raw)
            except ValueError:
                pass
            proposed_title = stripped
            proposed_series = stripped  # series name = cleaned title

    changed = (
        proposed_title != (book.title or "")
        or proposed_subtitle != book.subtitle
        or proposed_series != (book.series or None)
        or proposed_series_index != book.series_index
    )

    return {
        "book_id": book.id,
        "current_title": book.title or "",
        "current_subtitle": book.subtitle,
        "current_series": book.series,
        "current_series_index": book.series_index,
        "proposed_title": proposed_title,
        "proposed_subtitle": proposed_subtitle,
        "proposed_series": proposed_series,
        "proposed_series_index": proposed_series_index,
        "changed": changed,
    }


class _StandardizeRequest(PydanticBaseModel):
    book_ids: list[int]


@router.post("/standardize-titles")
def standardize_titles(
    body: _StandardizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview title standardization proposals. Does NOT apply changes."""
    require_role(current_user, "admin")

    books = db.query(Book).filter(Book.id.in_(body.book_ids)).all()
    return [_standardize_book_title(b) for b in books]


# ── Library health ───────────────────────────────────────────────────────────

class ReorganizeRequest(PydanticBaseModel):
    file_ids: list[int]
    dry_run: bool = False


@router.post("/purge-empty-dirs")
def purge_empty_dirs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Walk library_dir bottom-up and remove dirs that contain only hidden files. Admin only."""
    require_role(current_user, "admin")

    removed: list[str] = []
    # Walk bottom-up so child dirs are processed before parents
    for dirpath, dirnames, filenames in __import__('os').walk(settings.library_dir, topdown=False):
        current = Path(dirpath)
        if current == settings.library_dir:
            continue
        entries = list(current.iterdir())
        non_hidden = [e for e in entries if not e.name.startswith('.')]
        if non_hidden:
            continue
        # Only hidden files (e.g. .DS_Store) — delete them then remove the dir
        for e in entries:
            e.unlink(missing_ok=True)
        try:
            current.rmdir()
            removed.append(str(current.relative_to(settings.library_dir)))
        except OSError:
            pass  # not empty after all, skip

    if removed:
        audit(db, "purge_empty_dirs",
              user_id=current_user.id,
              username=current_user.username,
              details={"removed": len(removed)})

    return {"removed": removed}


def _cleanup_empty_dirs(start: Path, stop_at: Path, removed: list[str]) -> None:
    """Remove empty dirs from start up to (but not including) stop_at."""
    current = start
    while current != stop_at and current.is_relative_to(stop_at):
        if current.is_dir() and not any(f for f in current.iterdir() if not f.name.startswith('.')):
            rel = str(current.relative_to(stop_at))
            current.rmdir()
            removed.append(rel)
            current = current.parent
        else:
            break


@router.get("/library-health")
def library_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compare actual file locations against organizer rules. Admin only."""
    require_role(current_user, "admin")

    from sqlalchemy.orm import joinedload as _jl
    books = (
        db.query(Book)
        .options(_jl(Book.files))
        .filter(Book.status == "active")
        .all()
    )

    issues = []
    for book in books:
        meta = {
            "title": book.title,
            "author": book.author,
            "series": book.series,
            "series_index": book.series_index,
            "year": book.year,
        }
        for bf in book.files:
            actual_path = Path(bf.file_path)
            expected_rel = get_library_path(meta, actual_path.name)
            expected_abs = settings.library_dir / expected_rel

            if actual_path.resolve() != expected_abs.resolve():
                issues.append({
                    "book_id": book.id,
                    "file_id": bf.id,
                    "title": book.title or "",
                    "author": book.author or "",
                    "series": book.series or "",
                    "series_index": book.series_index,
                    "format": bf.format,
                    "current_path": str(actual_path.relative_to(settings.library_dir)) if actual_path.is_relative_to(settings.library_dir) else str(actual_path),
                    "expected_path": str(expected_rel),
                })

    total_files = sum(len(b.files) for b in books)
    return {
        "total_files": total_files,
        "misplaced_count": len(issues),
        "issues": issues,
    }


@router.post("/reorganize")
def reorganize_files(
    req: ReorganizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move book files to match current metadata. Admin only."""
    require_role(current_user, "admin")

    files = db.query(BookFile).filter(BookFile.id.in_(req.file_ids)).all()
    book_ids = {bf.book_id for bf in files}
    books = {b.id: b for b in db.query(Book).filter(Book.id.in_(book_ids)).all()}

    moved: list[dict] = []
    errors: list[dict] = []
    affected_dirs: set[Path] = set()

    for bf in files:
        book = books[bf.book_id]
        meta = {
            "title": book.title,
            "author": book.author,
            "series": book.series,
            "series_index": book.series_index,
            "year": book.year,
        }
        actual = Path(bf.file_path)
        expected_rel = get_library_path(meta, actual.name)
        expected_abs = resolve_unique_path(settings.library_dir, expected_rel)

        if actual.resolve() == (settings.library_dir / expected_rel).resolve():
            continue  # already correct

        from_rel = str(actual.relative_to(settings.library_dir)) if actual.is_relative_to(settings.library_dir) else str(actual)
        to_rel = str(expected_abs.relative_to(settings.library_dir)) if expected_abs.is_relative_to(settings.library_dir) else str(expected_abs)

        if req.dry_run:
            moved.append({"file_id": bf.id, "from": from_rel, "to": to_rel})
            continue

        try:
            expected_abs.parent.mkdir(parents=True, exist_ok=True)
            actual.rename(expected_abs)
            affected_dirs.add(actual.parent)
            bf.file_path = str(expected_abs)
            moved.append({"file_id": bf.id, "from": from_rel, "to": to_rel})
        except Exception as e:
            errors.append({"file_id": bf.id, "error": str(e)})

    folders_removed: list[str] = []
    if not req.dry_run:
        db.commit()
        for d in affected_dirs:
            _cleanup_empty_dirs(d, settings.library_dir, folders_removed)
        if moved:
            audit(db, "reorganize_files",
                  user_id=current_user.id,
                  username=current_user.username,
                  details={"moved": len(moved), "folders_removed": len(folders_removed)})

    return {"moved": moved, "errors": errors, "folders_removed": folders_removed}


# ── Metadata audit ───────────────────────────────────────────────────────────

@router.get("/metadata-audit")
def metadata_audit(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns all books with completeness scores for the admin metadata manager."""
    require_role(current_user, "admin")

    from sqlalchemy.orm import joinedload as _jl
    books = (
        db.query(Book)
        .options(_jl(Book.book_type), _jl(Book.libraries), _jl(Book.files))
        .filter(Book.status == "active")
        .filter(Book.content_type != "chapter")
        .all()
    )

    results = []
    for book in books:
        fields = {
            "title": bool(book.title),
            "author": bool(book.author),
            "series": bool(book.series),
            "year": bool(book.year),
            "description": bool(book.description),
            "cover": bool(book.cover_path),
            "isbn": bool(book.isbn),
            "language": bool(book.language),
            "publisher": bool(book.publisher),
        }
        results.append({
            "id": book.id,
            "title": book.title or "",
            "author": book.author or "",
            "series": book.series or "",
            "series_index": book.series_index,
            "year": book.year,
            "subtitle": book.subtitle or "",
            "description_snippet": (book.description or "")[:120],
            "isbn": book.isbn or "",
            "language": book.language or "",
            "publisher": book.publisher or "",
            "cover_path": book.cover_path,
            "book_type_label": book.book_type.label if book.book_type else None,
            "book_type_id": book.book_type_id,
            "content_type": book.content_type,
            "library_ids": [lib.id for lib in book.libraries],
            "fields_present": fields,
            "completeness": sum(fields.values()),
            "completeness_total": len(fields),
        })

    return results


# ── Adjacent book navigation ──────────────────────────────────────────────────

@router.get("/{book_id}/adjacent")
def get_adjacent_books(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    def to_stub(b: Book | None) -> dict | None:
        if b is None:
            return None
        return {"id": b.id, "title": b.title, "series_index": b.series_index, "cover_path": b.cover_path}

    if book.series:
        peers = (
            db.query(Book)
            .filter(Book.series == book.series, Book.status == "active")
            .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
            .all()
        )
        mode = "series"
    elif book.author:
        peers = (
            db.query(Book)
            .filter(Book.author == book.author, Book.status == "active")
            .order_by(Book.title.asc())
            .all()
        )
        mode = "author"
    else:
        return {"prev": None, "next": None, "mode": None}

    ids = [b.id for b in peers]
    try:
        pos = ids.index(book_id)
    except ValueError:
        return {"prev": None, "next": None, "mode": None}

    prev_book = peers[pos - 1] if pos > 0 else None
    next_book = peers[pos + 1] if pos < len(peers) - 1 else None

    return {"prev": to_stub(prev_book), "next": to_stub(next_book), "mode": mode}


# ── Single book ───────────────────────────────────────────────────────────────

@router.get("/{book_id}", response_model=BookDetailOut)
def get_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from backend.core.permissions import is_admin as _is_admin
    from sqlalchemy import or_
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    # Visibility check for non-admins
    if not _is_admin(current_user):
        from backend.models.library import library_users_table
        admin_ids = {
            u.id for u in db.query(User).filter(
                (User.is_admin == True) | (User.role == "admin")
            ).all()
        }
        is_admin_book = book.added_by is None or book.added_by in admin_ids
        is_own_book = book.added_by == current_user.id
        if current_user.role == "member":
            assigned_lib_ids = {
                row[1] for row in db.execute(
                    library_users_table.select().where(
                        library_users_table.c.user_id == current_user.id
                    )
                ).fetchall()
            }
            book_lib_ids = {lib.id for lib in book.libraries}
            in_assigned_library = bool(assigned_lib_ids & book_lib_ids)
            if not (is_admin_book or is_own_book or in_assigned_library):
                raise HTTPException(status_code=404, detail="Book not found")
        else:
            # Guest
            in_public_library = any(lib.is_public for lib in book.libraries)
            if not (is_admin_book or in_public_library):
                raise HTTPException(status_code=404, detail="Book not found")
    return book


# ── Bulk fetch metadata ───────────────────────────────────────────────────────

class BulkFetchRequest(PydanticBaseModel):
    book_ids: list[int]


class BulkCandidateResult(PydanticBaseModel):
    book_id: int
    book_title: str
    book_author: Optional[str] = None
    book_cover_path: Optional[str] = None
    book_description: Optional[str] = None
    book_publisher: Optional[str] = None
    book_year: Optional[int] = None
    book_series: Optional[str] = None
    book_series_index: Optional[float] = None
    book_content_type: Optional[str] = None
    candidates: list[MetadataCandidateOut]
    best_match_index: Optional[int] = None  # index into candidates, None = no confident match


def _extract_vol_number(title: str) -> int | None:
    """Extract volume number from a title string. Handles 'v001', 'Vol. 1', 'Vol 1', 'Volume 1'."""
    m = re.search(r'\bv(?:ol(?:ume)?\.?\s*)(\d+)\b', title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Also match bare "vNNN" (manga filename convention)
    m = re.search(r'\bv(\d{2,4})\b', title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _score_candidate(candidate, book: Book) -> int:
    from backend.services.metadata_fetch import _clean_title
    score = 0
    if candidate.description:
        score += 2
    if candidate.cover_url:
        score += 1
    if candidate.isbn and book.isbn and candidate.isbn == book.isbn:
        score += 4
    if candidate.year and book.year and candidate.year == book.year:
        score += 1

    # Compare against cleaned title (strips filename noise like "(Digital) (1r0n)")
    clean_book_title = _clean_title(book.title).lower() if book.title else ""
    if candidate.title and clean_book_title:
        ratio = difflib.SequenceMatcher(
            None, candidate.title.lower(), clean_book_title,
        ).ratio()
        if ratio > 0.85:
            score += 3
        elif ratio > 0.6:
            score += 1
    if candidate.author and book.author:
        ratio = difflib.SequenceMatcher(
            None, candidate.author.lower(), book.author.lower()
        ).ratio()
        if ratio > 0.7:
            score += 2

    # Volume number matching — critical for series like manga where all titles
    # start with the same series name. Extract the volume from the book's raw
    # title (e.g. "My Series v004") and check if the candidate matches.
    book_vol = _extract_vol_number(book.title) if book.title else None
    if book_vol is not None:
        # Check candidate's series_index first (most reliable)
        cand_vol = None
        if candidate.series_index is not None:
            cand_vol = int(candidate.series_index) if candidate.series_index == int(candidate.series_index) else None
        # Fall back to extracting from candidate title
        if cand_vol is None:
            cand_vol = _extract_vol_number(candidate.title) if candidate.title else None
        if cand_vol == book_vol:
            score += 8  # strong signal — right volume
        else:
            score -= 4  # wrong volume is worse than no match

    # Prefer English-language results (or results matching the book's language)
    book_lang = (book.language or "en").lower()[:2]
    cand_lang = (candidate.language or "en").lower()[:2]
    if cand_lang == book_lang:
        score += 2
    elif cand_lang != "en":
        score -= 3  # penalise non-English when book language unknown

    # Penalise omnibus editions and spin-offs — they share volume numbers with
    # the main series but are different books
    if candidate.title:
        ct = candidate.title.lower()
        if "omnibus" in ct:
            score -= 3
        if "ace's story" in ct or "film:" in ct or "color walk" in ct:
            score -= 5

    # Source priority: Hardcover has best quality metadata for manga/LN/books
    if candidate.source == "hardcover":
        score += 6

    return score


@router.post("/bulk-fetch-candidates", response_model=list[BulkCandidateResult])
async def bulk_fetch_candidates(
    body: BulkFetchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch metadata candidates for multiple books for user review. Does not apply anything."""
    require_role(current_user, "member")

    if len(body.book_ids) > 100:
        raise HTTPException(status_code=400, detail="Too many books (max 100)")

    books = db.query(Book).filter(Book.id.in_(body.book_ids)).all()
    # Skip chapters — they don't have meaningful metadata on external sources
    books = [b for b in books if b.content_type != "chapter"]
    book_map = {b.id: b for b in books}

    import asyncio as _asyncio

    async def fetch_for_book(book: Book):
        try:
            result = await fetch_candidates(
                title=book.title,
                author=book.author,
                isbn=book.isbn,
                series=book.series,
                series_index=book.series_index,
            )
            candidates = result.candidates
        except Exception as e:
            logger.warning("bulk_fetch_candidates failed for book %d: %s", book.id, e)
            candidates = []

        best_index = None
        if candidates:
            scores = [(_score_candidate(c, book), i) for i, c in enumerate(candidates)]
            best_score, best_i = max(scores, key=lambda x: (x[0], -x[1]))
            if best_score >= 4:
                best_index = best_i

        return BulkCandidateResult(
            book_id=book.id,
            book_title=book.title,
            book_author=book.author,
            book_cover_path=book.cover_path,
            book_description=book.description,
            book_publisher=book.publisher,
            book_year=book.year,
            book_series=book.series,
            book_series_index=book.series_index,
            candidates=[MetadataCandidateOut.model_validate(c.__dict__) for c in candidates],
            best_match_index=best_index,
        )

    results = await _asyncio.gather(*[fetch_for_book(book_map[bid]) for bid in body.book_ids if bid in book_map])
    return list(results)


@router.post("/bulk-fetch-metadata")
async def bulk_fetch_metadata(
    body: BulkFetchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """For each book, fetch the best metadata candidate and fill any empty fields."""
    require_role(current_user, "member")

    if len(body.book_ids) > 100:
        raise HTTPException(status_code=400, detail="Too many books (max 100)")

    books = db.query(Book).filter(Book.id.in_(body.book_ids)).all()
    # Skip chapters — they don't have meaningful metadata on external sources
    books = [b for b in books if b.content_type != "chapter"]
    updated = 0
    no_match = 0
    errors = 0

    for book in books:
        try:
            result = await fetch_candidates(
                title=book.title,
                author=book.author,
                isbn=book.isbn,
                series=book.series,
                series_index=book.series_index,
            )
            candidates = result.candidates
            if not candidates:
                no_match += 1
                continue

            best = candidates[0]
            changed = False

            # Only fill fields that are currently empty
            simple_fields = ("author", "description", "publisher", "year", "language", "isbn", "series", "series_index")
            for f in simple_fields:
                if getattr(book, f) is None and getattr(best, f) is not None:
                    setattr(book, f, getattr(best, f))
                    changed = True

            # Add tags (never remove existing ones)
            if best.tags:
                existing = {t.tag for t in book.tags}
                for tag in best.tags:
                    tag = tag.strip()
                    if tag and tag not in existing:
                        book.tags.append(BookTag(book_id=book.id, tag=tag, source="metadata_fetch"))
                        changed = True

            # Download cover only if book has none
            if best.cover_url and not book.cover_path:
                try:
                    cover_data = await fetch_safe_image(best.cover_url)
                    cover_filename = f"book_{book.id}_fetched.jpg"
                    cover_path = settings.covers_dir / cover_filename
                    cover_path.parent.mkdir(parents=True, exist_ok=True)
                    cover_path.write_bytes(cover_data)
                    book.cover_path = cover_filename
                    changed = True
                except Exception:
                    pass  # cover fetch failure is non-fatal

            if changed:
                updated += 1
            else:
                no_match += 1  # found a candidate but nothing new to fill

        except Exception as e:
            logger.warning("Bulk fetch failed for book %d: %s", book.id, e)
            errors += 1

    db.commit()
    return {"updated": updated, "no_match": no_match, "errors": errors}


# ── Bulk metadata update ──────────────────────────────────────────────────────

class BulkMetadataUpdate(PydanticBaseModel):
    book_ids: list[int]
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    tags: Optional[list[str]] = None       # if set, REPLACE tags on all selected books
    tags_add: Optional[list[str]] = None   # if set, ADD these tags without removing existing
    book_type_id: Optional[int] = None


@router.put("/bulk-metadata")
def bulk_update_metadata(
    body: BulkMetadataUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "member")

    bt = None
    if body.book_type_id is not None:
        from backend.models.library import BookType
        bt = db.get(BookType, body.book_type_id)
        if not bt:
            raise HTTPException(status_code=400, detail="Invalid book_type_id")

    books = db.query(Book).filter(Book.id.in_(body.book_ids)).all()
    for book in books:
        if body.author is not None:
            book.author = body.author or None
        if body.series is not None:
            book.series = body.series or None
        if body.series_index is not None:
            book.series_index = body.series_index
        if body.tags is not None:
            book.tags = [BookTag(book_id=book.id, tag=t.strip(), source="user") for t in body.tags if t.strip()]
        elif body.tags_add:
            existing = {t.tag for t in book.tags}
            for t in body.tags_add:
                if t.strip() and t.strip() not in existing:
                    book.tags.append(BookTag(book_id=book.id, tag=t.strip(), source="user"))
        if bt is not None:
            book.book_type_id = bt.id
    db.commit()

    if bt is not None:
        from backend.services.book_types import assign_book_to_type_library
        for book in books:
            assign_book_to_type_library(db, book, bt)

    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "book_ids"}
    audit(db, "books.bulk_metadata_edited", user_id=current_user.id, username=current_user.username,
          details={"book_ids": body.book_ids, "count": len(books), "changes": changes})
    return {"updated": len(books)}


# ── Update metadata ───────────────────────────────────────────────────────────

@router.put("/{book_id}", response_model=BookDetailOut)
def update_book(
    book_id: int,
    body: BookUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "member")

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Members can only edit their own uploads; admins can edit any book
    from backend.core.permissions import is_admin as _is_admin
    if not _is_admin(current_user) and book.added_by != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit books you uploaded")

    data = body.model_dump(exclude_unset=True)
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
            raise HTTPException(status_code=400, detail="Invalid book_type_id")
        book.book_type_id = new_type_id
        assign_book_to_type_library(db, book, bt)

    db.commit()
    db.refresh(book)
    audit(db, "books.metadata_edited", user_id=current_user.id, username=current_user.username,
          resource_type="book", resource_id=book.id, resource_title=book.title,
          details={k: v for k, v in body.model_dump(exclude_unset=True).items()})
    return book


# ── Cover ─────────────────────────────────────────────────────────────────────

@router.get("/{book_id}/cover")
def get_cover(
    book_id: int,
    db: Session = Depends(get_db),
):
    """Serve book cover — no auth required (covers are not sensitive)."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book or not book.cover_path:
        raise HTTPException(status_code=404, detail="Cover not found")

    cover_file = settings.covers_dir / book.cover_path
    if not cover_file.exists():
        raise HTTPException(status_code=404, detail="Cover file missing")

    return FileResponse(str(cover_file), media_type="image/jpeg")


class CoverCandidateOut(PydanticBaseModel):
    source: str   # "hardcover" | "google_books" | "open_library"
    label: str
    cover_url: str


@router.get("/{book_id}/cover-candidates", response_model=list[CoverCandidateOut])
async def get_cover_candidates(
    book_id: int,
    q: Optional[str] = Query(None, description="Override search query"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch cover image candidates from Hardcover, Google Books and OpenLibrary in parallel."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    import asyncio
    from backend.services.metadata_fetch import _hardcover, _google_books, _open_library, _build_query

    query_override = q or None
    effective_isbn = None if query_override else book.isbn
    query = _build_query(book.title, book.author, effective_isbn,
                         book.series, book.series_index, query_override)

    async with httpx.AsyncClient(timeout=10) as client:
        hc_results, gb_results, ol_results = await asyncio.gather(
            _hardcover(client, book.title, book.author, effective_isbn,
                       book.series, book.series_index, query_override),
            _google_books(client, query, effective_isbn),
            _open_library(client, query, effective_isbn),
            return_exceptions=True,
        )

    candidates: list[CoverCandidateOut] = []
    seen_urls: set[str] = set()

    # Hardcover first — best cover quality
    source_map = [
        (hc_results, "hardcover", "Hardcover"),
        (gb_results, "google_books", "Google Books"),
        (ol_results, "open_library", "OpenLibrary"),
    ]
    for results, source_key, source_label in source_map:
        if isinstance(results, Exception):
            logger.warning("Cover candidate fetch failed for %s: %s", source_key, results)
            continue
        for c in results:
            if c.cover_url and c.cover_url not in seen_urls:
                seen_urls.add(c.cover_url)
                title_snippet = c.title[:40] if c.title else ""
                candidates.append(CoverCandidateOut(
                    source=source_key,
                    label=f"{source_label} — {title_snippet}",
                    cover_url=c.cover_url,
                ))

    return candidates


@router.post("/{book_id}/cover", response_model=BookDetailOut)
async def set_book_cover(
    book_id: int,
    url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace a book's cover — accepts either a remote URL or a file upload."""
    require_role(current_user, "member")

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if not url and not file:
        raise HTTPException(status_code=400, detail="Provide either url or file")

    # Remove old cover file
    if book.cover_path:
        old_file = settings.covers_dir / book.cover_path
        old_file.unlink(missing_ok=True)

    cover_filename = f"book_{book_id}_cover.jpg"
    cover_path = settings.covers_dir / cover_filename
    cover_path.parent.mkdir(parents=True, exist_ok=True)

    if url:
        try:
            data = await fetch_safe_image(url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cover URL: {exc}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Cover download failed: {exc}")
        cover_path.write_bytes(data)
    else:
        data = await file.read()
        cover_path.write_bytes(data)

    book.cover_path = cover_filename
    db.commit()
    db.refresh(book)
    return book


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/{book_id}/download/{file_id}")
def download_book(
    book_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    book_file = (
        db.query(BookFile)
        .filter(BookFile.id == file_id, BookFile.book_id == book_id)
        .first()
    )
    if not book_file:
        raise HTTPException(status_code=404, detail="File not found")

    from backend.core.permissions import user_can_see_book
    if not user_can_see_book(db, current_user, book_file.book):
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(book_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    filename = f"{book_file.book.title}.{book_file.format}"
    audit(db, "books.downloaded", user_id=current_user.id, username=current_user.username,
          resource_type="book", resource_id=book_id, resource_title=book_file.book.title,
          details={"format": book_file.format, "filename": filename})
    return FileResponse(
        str(file_path),
        media_type="application/octet-stream",
        filename=filename,
    )


# ── Comic page streaming ──────────────────────────────────────────────────────

_page_list_cache: dict[str, tuple[float, list[str]]] = {}

_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.avif')
_MEDIA_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'webp': 'image/webp', 'gif': 'image/gif', 'bmp': 'image/bmp', 'avif': 'image/avif',
}


def _get_comic_pages_zip(file_path: Path) -> list[str]:
    """Get sorted image filenames from a CBZ archive, with mtime-based caching."""
    import zipfile
    key = str(file_path)
    mtime = file_path.stat().st_mtime
    cached = _page_list_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    with zipfile.ZipFile(file_path, 'r') as zf:
        images = sorted(
            n for n in zf.namelist()
            if n.lower().endswith(_IMAGE_EXTS) and not n.startswith('__MACOSX')
        )
    _page_list_cache[key] = (mtime, images)
    return images


@router.get("/{book_id}/pages")
def get_comic_pages(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get page list for a comic book (CBZ/CBR)."""
    comic_file = db.query(BookFile).filter(
        BookFile.book_id == book_id,
        BookFile.format.in_(["cbz", "cbr"]),
    ).first()
    if not comic_file:
        raise HTTPException(status_code=404, detail="No comic file for this book")

    file_path = Path(comic_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    if comic_file.format == "cbr":
        try:
            import rarfile
            with rarfile.RarFile(str(file_path)) as rf:
                images = sorted(
                    n for n in rf.namelist()
                    if n.lower().endswith(_IMAGE_EXTS)
                )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read CBR: {exc}")
        return {"total": len(images), "pages": [{"index": i, "filename": n} for i, n in enumerate(images)]}

    images = _get_comic_pages_zip(file_path)
    return {"total": len(images), "pages": [{"index": i, "filename": n} for i, n in enumerate(images)]}


@router.get("/{book_id}/pages/{page_index}")
def get_comic_page(
    book_id: int,
    page_index: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Serve a single page image from a comic book archive.
    Uses ?token= query param auth (img tags can't send Authorization headers)."""
    from jose import JWTError, jwt as jose_jwt
    from backend.core.config import settings as app_settings
    from backend.models.user import User as UserModel

    try:
        payload = jose_jwt.decode(token, app_settings.secret_key, algorithms=[app_settings.jwt_algorithm])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.get(UserModel, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    comic_file = db.query(BookFile).filter(
        BookFile.book_id == book_id,
        BookFile.format.in_(["cbz", "cbr"]),
    ).first()
    if not comic_file:
        raise HTTPException(status_code=404, detail="No comic file for this book")

    file_path = Path(comic_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    if comic_file.format == "cbr":
        try:
            import rarfile
            with rarfile.RarFile(str(file_path)) as rf:
                images = sorted(
                    n for n in rf.namelist()
                    if n.lower().endswith(_IMAGE_EXTS)
                )
                if page_index < 0 or page_index >= len(images):
                    raise HTTPException(status_code=404, detail="Page not found")
                data = rf.read(images[page_index])
                ext = images[page_index].rsplit('.', 1)[-1].lower()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read CBR: {exc}")
        media = _MEDIA_TYPES.get(ext, 'application/octet-stream')
        return Response(content=data, media_type=media, headers={"Cache-Control": "public, max-age=86400"})

    import zipfile
    images = _get_comic_pages_zip(file_path)
    if page_index < 0 or page_index >= len(images):
        raise HTTPException(status_code=404, detail="Page not found")
    with zipfile.ZipFile(file_path, 'r') as zf:
        data = zf.read(images[page_index])
    ext = images[page_index].rsplit('.', 1)[-1].lower()
    media = _MEDIA_TYPES.get(ext, 'application/octet-stream')
    return Response(content=data, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{book_id}/read.cbz")
def read_cbz(book_id: int, token: str = Query(...), db: Session = Depends(get_db)):
    """Serve CBZ/CBR file for the comic reader."""
    from jose import JWTError, jwt as jose_jwt
    from backend.core.security import _signing_key
    from backend.models.user import User as UserModel

    try:
        payload = jose_jwt.decode(token, _signing_key(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(UserModel, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    # Find CBZ or CBR file
    comic_file = db.query(BookFile).filter(
        BookFile.book_id == book_id,
        BookFile.format.in_(["cbz", "cbr"])
    ).first()
    if not comic_file:
        raise HTTPException(status_code=404, detail="No comic file for this book")

    from backend.core.permissions import user_can_see_book
    if not user_can_see_book(db, user, comic_file.book):
        raise HTTPException(status_code=404, detail="No comic file for this book")

    file_path = Path(comic_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # CBR needs repacking to ZIP for the frontend reader
    if comic_file.format == "cbr":
        import rarfile
        import zipfile

        cache_dir = Path(settings.data_dir) / "cache"
        cache_dir.mkdir(exist_ok=True)
        cached = cache_dir / f"{comic_file.content_hash or comic_file.id}.cbz"

        if not cached.exists():
            with rarfile.RarFile(str(file_path)) as rf:
                with zipfile.ZipFile(str(cached), 'w') as zf:
                    for entry in rf.infolist():
                        if not entry.is_dir():
                            zf.writestr(entry.filename, rf.read(entry))

        file_path = cached

    return FileResponse(str(file_path), media_type="application/x-cbz")


@router.get("/{book_id}/read.pdf")
def read_pdf(book_id: int, token: str = Query(...), db: Session = Depends(get_db)):
    """Serve PDF file for the reader."""
    from jose import JWTError, jwt as jose_jwt
    from backend.core.security import _signing_key
    from backend.models.user import User as UserModel

    try:
        payload = jose_jwt.decode(token, _signing_key(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(UserModel, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    pdf_file = db.query(BookFile).filter(
        BookFile.book_id == book_id,
        BookFile.format == "pdf"
    ).first()
    if not pdf_file:
        raise HTTPException(status_code=404, detail="No PDF file for this book")

    from backend.core.permissions import user_can_see_book
    if not user_can_see_book(db, user, pdf_file.book):
        raise HTTPException(status_code=404, detail="No PDF file for this book")

    file_path = Path(pdf_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(str(file_path), media_type="application/pdf")


@router.get("/{book_id}/read.epub")
def read_epub(
    book_id: int,
    token: str = Query(..., description="JWT token for epub.js auth"),
    db: Session = Depends(get_db),
):
    """Serve the EPUB file for in-browser reading. Accepts JWT as a query param
    so epub.js can fetch it by URL (it cannot send Authorization headers)."""
    from jose import JWTError, jwt as jose_jwt
    from backend.core.security import _signing_key
    from backend.models.user import User as UserModel

    try:
        payload = jose_jwt.decode(token, _signing_key(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    epub_file = (
        db.query(BookFile)
        .filter(BookFile.book_id == book_id, BookFile.format == "epub")
        .first()
    )
    if not epub_file:
        raise HTTPException(status_code=404, detail="No EPUB file for this book")

    from backend.core.permissions import user_can_see_book
    if not user_can_see_book(db, user, epub_file.book):
        raise HTTPException(status_code=404, detail="No EPUB file for this book")

    file_path = Path(epub_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    return FileResponse(str(file_path), media_type="application/epub+zip")


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "member")

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Members can only delete their own uploads; admins can delete any book
    from backend.core.permissions import is_admin as _is_admin
    if not _is_admin(current_user) and book.added_by != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete books you uploaded")

    # Remove book files from disk
    for bf in book.files:
        fp = Path(bf.file_path)
        if fp.exists():
            fp.unlink(missing_ok=True)
            # Remove parent directory if now empty
            try:
                fp.parent.rmdir()
            except OSError:
                pass

    # Remove cover file if it exists
    if book.cover_path:
        cover_file = settings.covers_dir / book.cover_path
        if cover_file.exists():
            cover_file.unlink(missing_ok=True)

    audit(db, "books.deleted", user_id=current_user.id, username=current_user.username,
          resource_type="book", resource_id=book.id, resource_title=book.title)
    db.delete(book)
    db.commit()




@router.post("/upload", response_model=BookDetailOut, status_code=status.HTTP_201_CREATED)
def upload_book(
    file: UploadFile = File(...),
    book_type_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "member")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {suffix}")

    # Write to a temp location first so we can extract metadata before deciding the path
    tmp_dir = settings.incoming_dir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / file.filename
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        file.file.close()

    # Duplicate check by hash
    content_hash = sha256_file(tmp_path)
    existing = db.query(Book).filter(Book.content_hash == content_hash).first()
    if existing:
        already_file = db.query(BookFile).filter(BookFile.content_hash == content_hash).first()
        if not already_file:
            meta = extract_metadata(tmp_path, settings.covers_dir)
            rel = get_library_path(meta, file.filename)
            dest = resolve_unique_path(settings.library_dir, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), str(dest))
            db.add(BookFile(
                book_id=existing.id,
                file_path=str(dest.resolve()),
                format=suffix,
                file_size=dest.stat().st_size,
                content_hash=content_hash,
            ))
        else:
            tmp_path.unlink(missing_ok=True)
        db.commit()
        db.refresh(existing)
        return existing

    # New book — extract metadata, determine library path, move
    meta = extract_metadata(tmp_path, settings.covers_dir)
    rel = get_library_path(meta, file.filename)
    dest = resolve_unique_path(settings.library_dir, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), str(dest))

    # Auto-detect book type if not explicitly provided
    if not book_type_id:
        from backend.models.library import BookType
        if meta.get("_is_manga"):
            manga_type = db.query(BookType).filter(BookType.slug == "manga").first()
            if manga_type:
                book_type_id = manga_type.id
        elif suffix in ("cbz", "cbr"):
            comic_type = db.query(BookType).filter(BookType.slug == "comic").first()
            if comic_type:
                book_type_id = comic_type.id

    book = Book(
        title=meta.get("title", Path(file.filename).stem),
        author=meta.get("author"),
        series=meta.get("series"),
        series_index=meta.get("series_index"),
        isbn=meta.get("isbn"),
        publisher=meta.get("publisher"),
        description=meta.get("description"),
        language=meta.get("language"),
        year=meta.get("year"),
        cover_path=meta.get("cover_path"),
        content_hash=content_hash,
        status="active",
        added_by=current_user.id,
        book_type_id=book_type_id,
    )
    db.add(book)
    db.flush()

    db.add(BookFile(
        book_id=book.id,
        file_path=str(dest.resolve()),
        format=suffix,
        file_size=dest.stat().st_size,
        content_hash=content_hash,
    ))

    # Create genre tags from ComicInfo.xml
    if meta.get("_genres"):
        from backend.models.book import BookTag
        for genre in meta["_genres"]:
            existing = db.query(BookTag).filter(
                BookTag.book_id == book.id,
                BookTag.tag == genre,
            ).first()
            if not existing:
                db.add(BookTag(book_id=book.id, tag=genre, source="comic_info"))

    db.commit()

    if book_type_id:
        from backend.models.library import BookType as BT
        from backend.services.book_types import assign_book_to_type_library
        bt = db.get(BT, book_type_id)
        if bt:
            assign_book_to_type_library(db, book, bt)

    db.refresh(book)
    audit(db, "books.uploaded", user_id=current_user.id, username=current_user.username,
          resource_type="book", resource_id=book.id, resource_title=book.title,
          details={"format": suffix})
    return book


# ── Scribe: batch-import helpers ──────────────────────────────────────────────

class CheckHashesRequest(PydanticBaseModel):
    hashes: list[str]


class CheckHashesResponse(PydanticBaseModel):
    existing: dict[str, int]  # hash -> book_id


@router.post("/check-hashes", response_model=CheckHashesResponse)
def check_hashes(
    body: CheckHashesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a map of content_hash -> book_id for hashes that already exist and
    are visible to the calling user.  Used by the Scribe batch-import skill to
    skip files that Tome already knows about."""
    from backend.core.permissions import book_visibility_filter

    if not body.hashes:
        return CheckHashesResponse(existing={})

    # Check Book.content_hash
    visibility = book_visibility_filter(db, current_user)
    book_rows = (
        db.query(Book.id, Book.content_hash)
        .filter(
            Book.status == "active",
            Book.content_hash.in_(body.hashes),
            visibility,
        )
        .all()
    )

    existing: dict[str, int] = {}
    for book_id, h in book_rows:
        if h and h not in existing:
            existing[h] = book_id

    # Also check BookFile.content_hash for hashes not already matched
    remaining = [h for h in body.hashes if h not in existing]
    if remaining:
        file_rows = (
            db.query(BookFile.content_hash, BookFile.book_id)
            .join(Book, Book.id == BookFile.book_id)
            .filter(
                Book.status == "active",
                BookFile.content_hash.in_(remaining),
                visibility,
            )
            .all()
        )
        for h, book_id in file_rows:
            if h and h not in existing:
                existing[h] = book_id

    return CheckHashesResponse(existing=existing)


class IngestMetadata(PydanticBaseModel):
    title: str
    subtitle: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    isbn: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    year: Optional[int] = None
    tags: Optional[list[str]] = None
    library_ids: Optional[list[int]] = None
    book_type_id: Optional[int] = None


@router.post("/ingest", response_model=BookDetailOut, status_code=status.HTTP_201_CREATED)
def ingest_book(
    file: UploadFile = File(...),
    metadata: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scribe endpoint: upload a file with caller-supplied metadata (no auto-extraction).

    - Caller is authoritative for all metadata fields.
    - Deduplication: if the file's content_hash already exists in DB, returns 409.
    - Library assignment: uses library_ids if provided, else the book_type's auto-library,
      else leaves unassigned.
    - Sets is_reviewed=True (caller has already reviewed the book).
    - Writes a ``book.ingest`` audit entry.
    """
    require_role(current_user, "member")

    # Parse the metadata JSON blob
    try:
        meta = IngestMetadata.model_validate_json(metadata)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid metadata JSON: {exc}") from exc

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {suffix}")

    # Write to a temp location so we can compute hash before deciding path
    tmp_dir = settings.incoming_dir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / file.filename
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        file.file.close()

    # Deduplicate by content hash
    content_hash = sha256_file(tmp_path)
    existing_book = (
        db.query(Book)
        .filter(
            (Book.content_hash == content_hash) | Book.files.any(BookFile.content_hash == content_hash)
        )
        .first()
    )
    if existing_book:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail={"detail": "duplicate", "existing_id": existing_book.id},
        )

    # Determine library path using organizer (same as upload endpoint)
    path_meta: dict = {
        "title": meta.title,
        "author": meta.author,
        "series": meta.series,
        "series_index": meta.series_index,
        "year": meta.year,
    }
    rel = get_library_path(path_meta, file.filename)
    dest = resolve_unique_path(settings.library_dir, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), str(dest))

    book = Book(
        title=meta.title,
        subtitle=meta.subtitle,
        author=meta.author,
        series=meta.series,
        series_index=meta.series_index,
        isbn=meta.isbn,
        publisher=meta.publisher,
        description=meta.description,
        language=meta.language,
        year=meta.year,
        content_hash=content_hash,
        status="active",
        is_reviewed=True,
        added_by=current_user.id,
        book_type_id=meta.book_type_id,
    )
    db.add(book)
    db.flush()

    db.add(BookFile(
        book_id=book.id,
        file_path=str(dest.resolve()),
        format=suffix,
        file_size=dest.stat().st_size,
        content_hash=content_hash,
    ))

    # Tags
    if meta.tags:
        for tag_str in meta.tags:
            tag_str = tag_str.strip()
            if tag_str:
                db.add(BookTag(book_id=book.id, tag=tag_str, source="user"))

    db.commit()

    # Library assignment
    if meta.library_ids:
        from backend.models.library import Library as Lib
        libs = db.query(Lib).filter(Lib.id.in_(meta.library_ids)).all()
        for lib in libs:
            if lib not in book.libraries:
                book.libraries.append(lib)
        db.commit()
    elif meta.book_type_id:
        from backend.models.library import BookType as BT
        from backend.services.book_types import assign_book_to_type_library
        bt = db.get(BT, meta.book_type_id)
        if bt:
            assign_book_to_type_library(db, book, bt)

    db.refresh(book)
    audit(
        db,
        "book.ingest",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="book",
        resource_id=book.id,
        resource_title=book.title,
        details={"format": suffix},
    )
    return book


# ── Fetch metadata candidates ─────────────────────────────────────────────────

@router.get("/{book_id}/fetch-metadata", response_model=list[MetadataCandidateOut])
async def fetch_metadata(
    book_id: int,
    q: Optional[str] = Query(None, description="Override search query"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Search external sources and return up to 5 metadata candidates for review."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    result = await fetch_candidates(
        title=book.title,
        author=book.author,
        isbn=book.isbn,
        series=book.series,
        series_index=book.series_index,
        query_override=q,
    )
    return result.candidates


# ── Apply selected metadata ───────────────────────────────────────────────────

@router.post("/{book_id}/apply-metadata", response_model=BookDetailOut)
async def apply_metadata(
    book_id: int,
    body: ApplyMetadataRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply only the explicitly passed fields from a chosen candidate."""
    require_role(current_user, "member")

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Apply scalar fields if provided
    simple_fields = ("title", "author", "description", "publisher", "year", "language", "isbn", "series", "series_index")
    for f in simple_fields:
        val = getattr(body, f)
        if val is not None:
            setattr(book, f, val)

    # Replace tags if provided
    if body.tags is not None:
        db.query(BookTag).filter(BookTag.book_id == book_id).delete()
        for tag in body.tags:
            tag = tag.strip()
            if tag:
                db.add(BookTag(book_id=book_id, tag=tag, source="metadata_fetch"))

    # Download and replace cover if requested
    if body.cover_url:
        try:
            cover_data = await fetch_safe_image(body.cover_url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cover URL: {exc}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Cover download failed: {exc}")

        cover_filename = f"book_{book_id}_fetched.jpg"
        cover_path = settings.covers_dir / cover_filename
        cover_path.parent.mkdir(parents=True, exist_ok=True)

        if book.cover_path:
            old = settings.covers_dir / book.cover_path
            old.unlink(missing_ok=True)

        cover_path.write_bytes(cover_data)
        book.cover_path = cover_filename

    db.commit()
    db.refresh(book)
    return book


# ── Batch status fetch ────────────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel
from backend.models.user_book_status import UserBookStatus


class StatusBatchRequest(_BaseModel):
    book_ids: list[int]


@router.post("/statuses")
def get_book_statuses(
    body: StatusBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    rows = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.book_id.in_(body.book_ids),
    ).all()
    return {str(row.book_id): {"status": row.status, "progress_pct": row.progress_pct} for row in rows}
