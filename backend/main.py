from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.core.database import engine, Base, init_fts, backfill_fts
from backend.core.config import settings
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
    init_fts(engine)
    backfill_fts(engine)
    settings.ensure_dirs()
    # Seed default book types (no-op if already seeded)
    from backend.core.database import SessionLocal
    from backend.services.book_types import seed_book_types
    with SessionLocal() as db:
        seed_book_types(db)
    yield


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
