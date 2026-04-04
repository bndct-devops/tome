from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from typing import Generator
from backend.core.config import settings


class Base(DeclarativeBase):
    pass


def _set_wal_mode(dbapi_connection, connection_record):
    """Enable WAL mode for safe concurrent reads."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine():
    settings.ensure_dirs()
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_wal_mode)
    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_fts(engine) -> None:
    """Create contentless FTS5 virtual table (idempotent).

    Uses content='' because the tags column comes from book_tags, not the books
    table. The entire index is rebuilt on every startup via backfill_fts().
    Triggers are not used — the startup rebuild is the single source of truth.
    """
    with engine.connect() as conn:
        # Drop and recreate if schema changed (e.g. added tags, or switching from content sync to contentless).
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='books_fts'"
        )).fetchone()
        if row and ("tags" not in (row[0] or "") or "content='books'" in (row[0] or "")):
            conn.execute(text("DROP TABLE IF EXISTS books_fts"))
            for trig in ("books_fts_insert", "books_fts_delete", "books_fts_update"):
                conn.execute(text(f"DROP TRIGGER IF EXISTS {trig}"))

        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title, author, series, description, tags,
                content=''
            )
        """))
        conn.commit()


def backfill_fts(engine) -> None:
    """Rebuild FTS index from books table on every startup to prevent stale data.

    Drops and recreates the contentless FTS table, then inserts with tags joined
    from book_tags.
    """
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS books_fts"))
        conn.execute(text("""
            CREATE VIRTUAL TABLE books_fts USING fts5(
                title, author, series, description, tags,
                content=''
            )
        """))
        conn.execute(text("""
            INSERT INTO books_fts(rowid, title, author, series, description, tags)
            SELECT
                b.id,
                COALESCE(b.title, ''),
                COALESCE(b.author, ''),
                COALESCE(b.series, ''),
                COALESCE(b.description, ''),
                COALESCE(GROUP_CONCAT(bt.tag, ' '), '')
            FROM books b
            LEFT JOIN book_tags bt ON bt.book_id = b.id
            WHERE b.status = 'active'
            GROUP BY b.id
        """))
        conn.commit()
