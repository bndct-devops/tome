"""
Library importer and scanner.

Two distinct operations:
  import_incoming(incoming_dir, library_dir, covers_dir, db) 
      — picks up new files from incoming/, moves them into library/,
        creates Book + BookFile DB entries.

  scan_library(library_dir, covers_dir, db)
      — walks library/ looking for files not yet in the DB
        (handles files added outside Tome, e.g. manual copy).
"""
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.book import Book, BookFile
from backend.services.metadata import SUPPORTED_FORMATS, extract_metadata, get_format, sha256_file
from backend.services.organizer import get_library_path, resolve_unique_path

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    found: int = 0
    added: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    added_ids: list[int] = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────────────

def import_incoming(
    incoming_dir: Path,
    library_dir: Path,
    covers_dir: Path,
    db: Session,
    added_by: Optional[int] = None,
) -> ScanResult:
    """
    Process all supported files in incoming_dir:
      1. extract metadata
      2. move to library_dir using organised path
      3. create DB entry
    """
    result = ScanResult()

    if not incoming_dir.exists():
        logger.warning("incoming_dir does not exist: %s", incoming_dir)
        return result

    all_files = _collect_files(incoming_dir)
    result.found = len(all_files)
    logger.info("Importer: found %d files in %s", result.found, incoming_dir)

    for file_path in sorted(all_files):
        try:
            _import_file(file_path, library_dir, covers_dir, db, added_by, result)
        except Exception as e:
            result.errors += 1
            result.error_details.append(f"{file_path.name}: {e}")
            logger.error("Error importing %s: %s", file_path, e)

    db.commit()
    return result


def scan_library(
    library_dir: Path,
    covers_dir: Path,
    db: Session,
    added_by: Optional[int] = None,
) -> ScanResult:
    """
    Walk library_dir and register any files not yet known to the DB.
    Does NOT move files — they're already in place.
    """
    result = ScanResult()

    if not library_dir.exists():
        logger.warning("library_dir does not exist: %s", library_dir)
        return result

    all_files = _collect_files(library_dir)
    result.found = len(all_files)
    logger.info("Scanner: found %d files in %s", result.found, library_dir)

    for file_path in sorted(all_files):
        try:
            _register_file(file_path, covers_dir, db, added_by, result)
        except Exception as e:
            result.errors += 1
            result.error_details.append(f"{file_path.name}: {e}")
            logger.error("Error scanning %s: %s", file_path, e)

    db.commit()
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _collect_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for fmt in SUPPORTED_FORMATS:
        files.extend(directory.rglob(f"*{fmt}"))
    return files


def _import_file(
    src: Path,
    library_dir: Path,
    covers_dir: Path,
    db: Session,
    added_by: Optional[int],
    result: ScanResult,
) -> None:
    fmt = get_format(src)
    if not fmt:
        result.skipped += 1
        return

    try:
        content_hash = sha256_file(src)
        file_size = src.stat().st_size
    except OSError as e:
        raise RuntimeError(f"Cannot read: {e}") from e

    # Duplicate check — skip if hash already in DB
    if _handle_duplicate(src, content_hash, fmt, file_size, db, result):
        return

    # Extract metadata (while still at original path)
    meta = extract_metadata(src, covers_dir)

    # Determine destination inside library
    rel_path = get_library_path(meta, src.name)
    dest = resolve_unique_path(library_dir, rel_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Move the file
    shutil.move(str(src), str(dest))
    logger.info("Moved %s → %s", src.name, dest.relative_to(library_dir))

    # Clean up empty parent dirs in incoming
    _remove_empty_parents(src.parent, stop_at=src.parent.parent)

    _create_book_entry(dest, meta, content_hash, fmt, file_size, db, added_by, result)
    result.added += 1


def _register_file(
    file_path: Path,
    covers_dir: Path,
    db: Session,
    added_by: Optional[int],
    result: ScanResult,
) -> None:
    abs_path = str(file_path.resolve())

    if db.query(BookFile).filter(BookFile.file_path == abs_path).first():
        result.skipped += 1
        return

    fmt = get_format(file_path)
    if not fmt:
        result.skipped += 1
        return

    try:
        content_hash = sha256_file(file_path)
        file_size = file_path.stat().st_size
    except OSError as e:
        raise RuntimeError(f"Cannot read: {e}") from e

    if _handle_duplicate(file_path, content_hash, fmt, file_size, db, result):
        return

    meta = extract_metadata(file_path, covers_dir)
    _create_book_entry(file_path, meta, content_hash, fmt, file_size, db, added_by, result)
    result.added += 1


def _handle_duplicate(
    file_path: Path,
    content_hash: str,
    fmt: str,
    file_size: int,
    db: Session,
    result: ScanResult,
) -> bool:
    """Return True if the file is a duplicate and was handled (skip/alt-format)."""
    existing_book = db.query(Book).filter(Book.content_hash == content_hash).first()
    if existing_book:
        # Same content as an existing book — add as alternate format if not already there
        already = db.query(BookFile).filter(BookFile.content_hash == content_hash).first()
        if not already:
            db.add(BookFile(
                book_id=existing_book.id,
                file_path=str(file_path.resolve()),
                format=fmt,
                file_size=file_size,
                content_hash=content_hash,
            ))
            logger.info("Alternate format %s linked to book %d", fmt, existing_book.id)
        result.skipped += 1
        return True

    existing_file_hash = db.query(BookFile).filter(BookFile.content_hash == content_hash).first()
    if existing_file_hash:
        parent = db.query(Book).filter(Book.id == existing_file_hash.book_id).first()
        if parent and parent.status == "active":
            parent.status = "duplicate_review"
        result.duplicates += 1
        logger.info("Duplicate hash for %s — flagged for review", file_path.name)
        return True

    return False


def _create_book_entry(
    file_path: Path,
    meta: dict,
    content_hash: str,
    fmt: str,
    file_size: int,
    db: Session,
    added_by: Optional[int],
    result: Optional[ScanResult] = None,
) -> Book:
    book = Book(
        title=meta.get("title", file_path.stem),
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
        added_by=added_by,
    )
    db.add(book)
    db.flush()

    if result is not None:
        result.added_ids.append(book.id)

    db.add(BookFile(
        book_id=book.id,
        file_path=str(file_path.resolve()),
        format=fmt,
        file_size=file_size,
        content_hash=content_hash,
    ))

    # Auto-assign book type based on metadata
    if not book.book_type_id:
        from backend.models.library import BookType

        if meta.get("_is_manga"):
            manga_type = db.query(BookType).filter(BookType.slug == "manga").first()
            if manga_type:
                book.book_type_id = manga_type.id
        elif fmt in ("cbz", "cbr"):
            comic_type = db.query(BookType).filter(BookType.slug == "comic").first()
            if comic_type:
                book.book_type_id = comic_type.id

    # Check library default type if still unassigned
    if not book.book_type_id and book.libraries:
        for lib in book.libraries:
            if lib.default_book_type_id:
                book.book_type_id = lib.default_book_type_id
                break

    # Create genre tags from ComicInfo.xml
    if meta.get("_genres"):
        from backend.models.book import BookTag
        for genre in meta["_genres"]:
            existing = db.query(BookTag).filter(
                BookTag.book_id == book.id,
                BookTag.tag == genre
            ).first()
            if not existing:
                db.add(BookTag(book_id=book.id, tag=genre, source="comic_info"))

    return book


def _remove_empty_parents(directory: Path, stop_at: Path) -> None:
    """Remove directory if empty, then walk up to stop_at."""
    try:
        if directory != stop_at and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            _remove_empty_parents(directory.parent, stop_at)
    except OSError:
        pass

