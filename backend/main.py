import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.core.database import engine, Base, init_fts, backfill_fts
from backend.core.config import settings
from backend.services.safe_fetch import fetch_safe_image, UnsafeURLError

logger = logging.getLogger(__name__)

from backend.api import health, auth, books, libraries, book_types
from backend.api import users  # noqa: F401
from backend.api import downloads
from backend.api import opds
from backend.api import opds_pins
from backend.api import kosync
from backend.api import tome_sync
from backend.api import stats
from backend.api import quick_connect
from backend.api import admin_duplicates
from backend.api import home
from backend.api import bindery
from backend.models.kosync import KOSyncUser, KOSyncProgress, OPDSPendingLink, ReadingHistory  # noqa: F401
from backend.models.opds_pin import OpdsPin  # noqa: F401
from backend.models.tome_sync import ApiKey, ReadingSession, TomeSyncPosition  # noqa: F401
from backend.models.user_book_status import UserBookStatus  # noqa: F401
from backend.models.audit_log import AuditLog  # noqa: F401
from backend.models.quick_connect import QuickConnectCode  # noqa: F401
from backend.models.duplicate_dismissal import DuplicateDismissal  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Add columns that create_all can't add to existing tables
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(books)")).fetchall()}
        if "content_type" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN content_type VARCHAR(16) DEFAULT 'volume' NOT NULL"))
            conn.commit()
        # Add is_reviewed — default 1 so ALL existing books are considered already reviewed
        if "is_reviewed" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN is_reviewed BOOLEAN NOT NULL DEFAULT 1"))
            conn.commit()
        user_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "role" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'guest'"))
            conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = 1"))
            conn.execute(text(
                "UPDATE users SET role = 'member' "
                "WHERE is_admin = 0 AND id IN ("
                "  SELECT user_id FROM user_permissions WHERE can_upload = 1"
                ")"
            ))
            conn.commit()
    init_fts(engine)
    backfill_fts(engine)
    settings.ensure_dirs()
    settings.resolve_secret_key()
    # Seed default book types (no-op if already seeded)
    from backend.core.database import SessionLocal
    from backend.services.book_types import seed_book_types
    with SessionLocal() as db:
        seed_book_types(db)

    # Start background auto-import task if enabled
    auto_import_task: asyncio.Task | None = None
    if settings.auto_import:
        logger.info(
            "Auto-import enabled — scanning bindery every %d seconds",
            settings.auto_import_interval,
        )
        auto_import_task = asyncio.create_task(_auto_import_loop())
    else:
        logger.info("Auto-import disabled (set TOME_AUTO_IMPORT=true to enable)")

    yield

    # Shutdown: cancel the background task cleanly
    if auto_import_task is not None:
        auto_import_task.cancel()
        try:
            await auto_import_task
        except asyncio.CancelledError:
            pass


async def _auto_import_loop() -> None:
    """Background task: periodically import files from the bindery into the library."""
    while True:
        try:
            await asyncio.sleep(settings.auto_import_interval)
            await _run_auto_import()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in auto-import loop")


async def _run_auto_import() -> None:
    """Scan the incoming directory and auto-import any new supported files.

    Split into two phases per file so the event loop stays free:
      1. Ingest (sync, in thread): hash, move, extract metadata, create DB record
      2. Enrich (async): fetch external metadata, then apply + download cover in thread
    """
    import shutil
    from backend.core.database import SessionLocal
    from backend.models.book import Book, BookFile, BookTag
    from backend.models.user import User
    from backend.services.metadata import extract_metadata, sha256_file
    from backend.services.organizer import get_library_path, resolve_unique_path
    from backend.services.metadata_fetch import fetch_candidates
    from backend.services.audit import audit
    from backend.services.book_types import assign_book_to_type_library

    SUPPORTED = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}

    incoming = settings.incoming_dir
    if not incoming.exists():
        return

    # Collect supported, non-hidden files
    files_to_import: list[Path] = []
    for p in incoming.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(incoming)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.suffix.lower() in SUPPORTED:
            files_to_import.append(p)

    if not files_to_import:
        return

    logger.info("Auto-import: found %d file(s) in bindery", len(files_to_import))

    # ------------------------------------------------------------------
    # Phase 1 helper — runs in a thread so it never blocks the event loop
    # ------------------------------------------------------------------

    def _ingest_file(file_path: Path) -> tuple[Book, str, str | None] | None:
        """Hash, move, extract metadata, create Book row. Returns (book, content_hash, admin_username) or None to skip."""
        with SessionLocal() as db:
            admin_user = db.query(User).filter(User.is_admin == True).first()  # noqa: E712
            added_by_id: int | None = admin_user.id if admin_user else None
            admin_username: str | None = admin_user.username if admin_user else None

            suffix = file_path.suffix.lower().lstrip(".")
            content_hash = sha256_file(file_path)

            # Skip if already in DB (by content hash)
            existing = db.query(Book).filter(Book.content_hash == content_hash).first()
            if existing:
                logger.info(
                    "Auto-import: skipping %s — already imported as book #%d",
                    file_path.name, existing.id,
                )
                return None

            # Also check by absolute file path (BookFile)
            abs_path = str(file_path.resolve())
            existing_file = db.query(BookFile).filter(BookFile.file_path == abs_path).first()
            if existing_file:
                logger.info("Auto-import: skipping %s — file path already registered", file_path.name)
                return None

            # Extract embedded metadata
            meta = extract_metadata(file_path, settings.covers_dir)

            # Determine destination in library
            rel_lib = get_library_path(meta, file_path.name)
            dest = resolve_unique_path(settings.library_dir, rel_lib)
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Move the file
            shutil.move(str(file_path), str(dest))
            logger.info("Auto-import: moved %s → %s", file_path.name, dest)

            file_size = dest.stat().st_size
            cover_path: str | None = meta.get("cover_path")

            # Create Book with is_reviewed=False
            book = Book(
                title=meta.get("title", dest.stem),
                author=meta.get("author"),
                series=meta.get("series"),
                series_index=meta.get("series_index"),
                isbn=meta.get("isbn"),
                publisher=meta.get("publisher"),
                description=meta.get("description"),
                language=meta.get("language"),
                year=meta.get("year"),
                cover_path=cover_path,
                content_hash=content_hash,
                content_type="volume",
                status="active",
                added_by=added_by_id,
                is_reviewed=False,
            )
            db.add(book)
            db.flush()

            db.add(BookFile(
                book_id=book.id,
                file_path=str(dest.resolve()),
                format=suffix,
                file_size=file_size,
                content_hash=content_hash,
            ))

            # Auto-assign book type
            if not book.book_type_id:
                from backend.models.library import BookType
                if meta.get("_is_manga"):
                    manga_type = db.query(BookType).filter(BookType.slug == "manga").first()
                    if manga_type:
                        book.book_type_id = manga_type.id
                elif suffix in ("cbz", "cbr"):
                    comic_type = db.query(BookType).filter(BookType.slug == "comic").first()
                    if comic_type:
                        book.book_type_id = comic_type.id

            # Genre tags from ComicInfo
            if meta.get("_genres"):
                for genre in meta["_genres"]:
                    db.add(BookTag(book_id=book.id, tag=genre, source="comic_info"))

            db.commit()

            # Assign to book-type library
            if book.book_type_id:
                from backend.models.library import BookType
                bt = db.get(BookType, book.book_type_id)
                if bt:
                    assign_book_to_type_library(db, book, bt)

            db.refresh(book)

            audit(
                db,
                "auto_import.imported",
                user_id=added_by_id,
                username=admin_username or "system",
                resource_type="book",
                resource_id=book.id,
                resource_title=book.title,
                details={"format": suffix, "source": "auto_import"},
            )

            # Clean up empty dirs left behind in bindery
            _cleanup_empty_dir(file_path.parent, incoming)

            logger.info(
                "Auto-import: imported book #%d '%s' (is_reviewed=False)",
                book.id, book.title,
            )

            # Detach identifiers needed for phase 2
            return book.id, book.title, book.author, book.isbn, book.series, book.series_index, content_hash

    # ------------------------------------------------------------------
    # Phase 2 helper — apply metadata + cover (sync, in thread)
    # ------------------------------------------------------------------

    def _apply_metadata(book_id: int, best, content_hash: str) -> None:
        with SessionLocal() as db:
            book = db.get(Book, book_id)
            if not book:
                return
            book.title = best.title or book.title
            book.author = best.author or book.author
            if best.description and not book.description:
                book.description = best.description
            if best.publisher and not book.publisher:
                book.publisher = best.publisher
            if best.year and not book.year:
                book.year = best.year
            if best.isbn and not book.isbn:
                book.isbn = best.isbn
            if best.language and not book.language:
                book.language = best.language
            if best.series and not book.series:
                book.series = best.series
            if best.series_index is not None and book.series_index is None:
                book.series_index = best.series_index
            # Download cover if we don't have one
            if best.cover_url and not book.cover_path:
                try:
                    cover_data = asyncio.run(fetch_safe_image(best.cover_url))
                    from backend.services.metadata import save_cover
                    book.cover_path = save_cover(cover_data, settings.covers_dir, content_hash)
                except (UnsafeURLError, Exception) as exc:
                    logger.warning(
                        "Auto-import: failed to download cover for %s: %s",
                        book.title, exc,
                    )
            # Add tags from metadata source
            for tag_str in best.tags:
                tag_str = tag_str.strip()
                if tag_str:
                    db.add(BookTag(
                        book_id=book.id,
                        tag=tag_str,
                        source=best.source,
                    ))
            db.commit()
            logger.info(
                "Auto-import: applied metadata from %s for book #%d '%s'",
                best.source, book.id, book.title,
            )

    # ------------------------------------------------------------------
    # Process each file: ingest in thread, fetch async, apply in thread
    # ------------------------------------------------------------------

    for file_path in sorted(files_to_import):
        try:
            result = await asyncio.to_thread(_ingest_file, file_path)
            if result is None:
                continue

            book_id, title, author, isbn, series, series_index, content_hash = result

            # Phase 2: fetch metadata (async — uses httpx.AsyncClient internally)
            try:
                fetch_result = await fetch_candidates(
                    title=title,
                    author=author,
                    isbn=isbn,
                    series=series,
                    series_index=series_index,
                )
                if fetch_result.candidates:
                    best = fetch_result.candidates[0]
                    if best.title and best.author:
                        await asyncio.to_thread(_apply_metadata, book_id, best, content_hash)
            except Exception as exc:
                logger.warning(
                    "Auto-import: metadata fetch failed for %s: %s", title, exc
                )

        except Exception:
            logger.exception("Auto-import: failed to import %s", file_path.name)


def _cleanup_empty_dir(start: Path, stop_at: Path) -> None:
    """Walk upward from start removing empty dirs, never removing stop_at itself."""
    current = start.resolve()
    stop = stop_at.resolve()
    while current != stop and current != current.parent:
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
            else:
                break
        except OSError:
            break
        current = current.parent


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tome",
        description="Self-hosted ebook library",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(home.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(libraries.router, prefix="/api")
    app.include_router(book_types.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(downloads.router, prefix="/api")
    app.include_router(opds.router)  # mounted at /opds, not /api
    app.include_router(opds_pins.router, prefix="/api")
    app.include_router(kosync.router, prefix="/api")  # mounted at /api/v1/
    app.include_router(tome_sync.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(quick_connect.router, prefix="/api")
    app.include_router(admin_duplicates.router, prefix="/api")
    app.include_router(bindery.router, prefix="/api/bindery", tags=["bindery"])

    # Serve frontend static files in production (SPA fallback)
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        from starlette.responses import FileResponse as _FileResponse

        index_html = frontend_dist / "index.html"

        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="static-assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Serve the actual file if it exists, otherwise index.html
            file = frontend_dist / full_path
            if full_path and file.is_file():
                return _FileResponse(str(file))
            return _FileResponse(str(index_html))

    return app


app = create_app()
