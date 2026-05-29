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
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.book import Book, BookFile
from backend.services.metadata import SUPPORTED_FORMATS, extract_metadata, get_format, sha256_file
from backend.services.organizer import get_library_path, resolve_unique_path

# Below this many new files, the serial path wins (process-pool startup isn't
# worth it). Above it, scan_library fans the CPU-bound extract/hash out to a
# process pool. DB writes always stay single-process (SQLite is single-writer).
_PARALLEL_MIN = 64

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


def _extract_for_file(args: tuple[str, str]) -> dict:
    """Worker (runs in a pool process): pure CPU/IO work for one file — hash,
    size, and metadata extraction (which also saves the cover, an independent
    file). NO database access; returns a picklable dict. Per-file exceptions are
    captured so one bad file never kills the batch.
    """
    file_path_str, covers_dir_str = args
    file_path = Path(file_path_str)
    try:
        fmt = get_format(file_path)
        if not fmt:
            return {"path": file_path_str, "skip": True}
        content_hash = sha256_file(file_path)
        file_size = file_path.stat().st_size
        meta = extract_metadata(file_path, Path(covers_dir_str))
        return {"path": file_path_str, "fmt": fmt, "hash": content_hash,
                "size": file_size, "meta": meta}
    except Exception as e:  # noqa: BLE001 — isolate per-file failures
        return {"path": file_path_str, "error": str(e)}


def _persist_extract(extract: dict, db: Session, added_by: Optional[int],
                     result: ScanResult) -> None:
    """Main-process DB work for one extract result: dedup + create. Always runs
    serially in the single writer process, inside the caller's transaction."""
    if extract.get("error"):
        result.errors += 1
        result.error_details.append(f"{Path(extract['path']).name}: {extract['error']}")
        return
    if extract.get("skip"):
        result.skipped += 1
        return
    file_path = Path(extract["path"])
    ch, size, fmt, meta = extract["hash"], extract["size"], extract["fmt"], extract["meta"]
    try:
        if _handle_duplicate(file_path, ch, fmt, size, db, result):
            return
        _create_book_entry(file_path, meta, ch, fmt, size, db, added_by, result)
        result.added += 1
    except Exception as e:
        result.errors += 1
        result.error_details.append(f"{file_path.name}: {e}")
        logger.error("Error persisting %s: %s", file_path, e)


def scan_library(
    library_dir: Path,
    covers_dir: Path,
    db: Session,
    added_by: Optional[int] = None,
    workers: Optional[int] = None,
) -> ScanResult:
    """Walk library_dir and register files not yet known to the DB.

    The CPU-bound extract/hash phase is fanned out across worker processes
    (workers > 1); every database write stays in THIS process, in one
    transaction, so SQLite's single-writer model is respected. Does NOT move
    files — they're already in place. Set workers=1 (TOME_SCAN_WORKERS=1) for
    the fully serial, in-process path.
    """
    result = ScanResult()

    if not library_dir.exists():
        logger.warning("library_dir does not exist: %s", library_dir)
        return result

    all_files = _collect_files(library_dir)
    result.found = len(all_files)
    logger.info("Scanner: found %d files in %s", result.found, library_dir)

    # One query instead of one-per-file: drop unsupported formats and files
    # already registered, leaving only the work to do.
    known = {row[0] for row in db.query(BookFile.file_path).all()}
    candidates: list[Path] = []
    for f in sorted(all_files):
        if get_format(f) is None or str(f.resolve()) in known:
            result.skipped += 1
        else:
            candidates.append(f)

    if workers is None:
        workers = settings.scan_workers
    covers_str = str(covers_dir)
    tasks = [(str(f), covers_str) for f in candidates]

    if workers and workers > 1 and len(candidates) >= _PARALLEL_MIN:
        logger.info("Scanner: extracting %d files across %d workers", len(tasks), workers)
        # 'spawn' (not fork): scans run inside FastAPI's threadpool, and forking
        # a multi-threaded process can inherit held locks (logging, malloc) and
        # deadlock a worker. spawn starts a clean interpreter — safe by design.
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            for extract in pool.map(_extract_for_file, tasks, chunksize=16):
                _persist_extract(extract, db, added_by, result)
    else:
        for t in tasks:
            _persist_extract(_extract_for_file(t), db, added_by, result)

    db.commit()
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _collect_files(directory: Path) -> list[Path]:
    # Single tree walk, filtered by extension — not one rglob per format (which
    # walked the whole tree N times; the dominant traversal cost at scale).
    return [p for p in directory.rglob("*") if p.suffix.lower() in SUPPORTED_FORMATS]


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

    # (Library-default book type is applied when a book is added to a library.
    # A freshly-created book has no library associations yet, so the old
    # `book.libraries` check here only ever fired an empty lazy-load per book.)

    # Create genre tags from ComicInfo.xml
    genres = meta.get("_genres") or []
    if genres:
        from backend.models.book import BookTag
        for genre in genres:
            db.add(BookTag(book_id=book.id, tag=genre, source="comic_info"))

    # Keep the FTS index in sync inline. Pass tags explicitly so a bulk scan
    # doesn't lazy-load book.tags per book (book.id is already set by the flush
    # above, so no extra flush is needed here).
    from backend.services.fts import index_book
    index_book(db, book, tags=genres)

    return book


def _remove_empty_parents(directory: Path, stop_at: Path) -> None:
    """Remove directory if empty, then walk up to stop_at."""
    try:
        if directory != stop_at and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            _remove_empty_parents(directory.parent, stop_at)
    except OSError:
        pass

