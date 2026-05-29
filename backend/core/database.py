from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from sqlalchemy.pool import NullPool
from typing import Generator
from backend.core.config import settings


class Base(DeclarativeBase):
    pass


def _set_wal_mode(dbapi_connection, connection_record):
    """Enable WAL mode for safe concurrent reads."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # Throughput-oriented pragmas. synchronous=NORMAL is durable under WAL
    # (only a power-loss may drop the last transaction). The larger page cache
    # and mmap keep hot indexes (e.g. content_hash dedup lookups) in memory.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA cache_size=-65536")   # 64 MB page cache
    cursor.execute("PRAGMA mmap_size=268435456")  # 256 MB
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_db_engine():
    settings.ensure_dirs()
    # NullPool: a fresh SQLite connection per checkout, closed on release.
    # SQLite connections are essentially free (no network handshake) and WAL
    # mode lets many readers run concurrently — so pooling buys nothing and a
    # bounded QueuePool causes outages under fan-out loads (e.g. a page that
    # fires dozens of parallel cover/list requests while two users are active).
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
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
    """Ensure books_fts exists as a STANDARD (self-contained) FTS5 table.

    Standard FTS5 (no ``content=`` option) supports DELETE/INSERT by rowid, which
    lets us maintain the index incrementally per book (see services/fts.py).
    Migrates any pre-existing table — the old contentless (``content=''``) or
    external-content (``content='books'``) variants, or one missing the tags
    column — by dropping and recreating it; backfill_fts() then repopulates it.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='books_fts'"
        )).fetchone()
        sql = (row[0] or "") if row else ""
        needs_recreate = (not row) or ("content=" in sql) or ("tags" not in sql)
        if row and needs_recreate:
            conn.execute(text("DROP TABLE IF EXISTS books_fts"))
            for trig in ("books_fts_insert", "books_fts_delete", "books_fts_update"):
                conn.execute(text(f"DROP TRIGGER IF EXISTS {trig}"))
        if needs_recreate:
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                    title, author, series, description, tags
                )
            """))
        conn.commit()


def backfill_fts(engine) -> None:
    """Rebuild the FTS index from the books table, but ONLY when it is out of
    sync (row counts differ). The index is maintained incrementally during
    runtime (services/fts.py), so a healthy index needs no rebuild — startup
    cost no longer grows with library size. A mismatch (fresh migration, or
    drift from a maintenance bug) triggers a one-shot rebuild that self-heals it.
    """
    with engine.connect() as conn:
        try:
            fts_n = conn.execute(text("SELECT count(*) FROM books_fts")).scalar() or 0
        except Exception:
            fts_n = -1
        book_n = conn.execute(
            text("SELECT count(*) FROM books WHERE status = 'active'")
        ).scalar() or 0
        if fts_n == book_n:
            return
        conn.execute(text("DELETE FROM books_fts"))
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
