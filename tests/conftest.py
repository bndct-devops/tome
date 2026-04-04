"""Test infrastructure for the Tome backend.

Uses an in-memory SQLite engine so tests run fast and never touch disk.
The `get_db` dependency is overridden on the FastAPI app so the TestClient
always talks to the same in-memory database as the fixtures.
"""
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from starlette.testclient import TestClient

# Import Base BEFORE create_app so all model metadata is registered
from backend.core.database import Base, get_db
from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission
from backend.models.book import Book, BookFile, BookTag


# ── In-memory test engine ─────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def _enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


event.listen(test_engine, "connect", _enable_foreign_keys)

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _init_test_db():
    """Create all tables (and the FTS virtual table) on the in-memory engine."""
    # Import all models so their metadata is registered with Base
    import backend.models.library  # noqa: F401
    import backend.models.user_book_status  # noqa: F401
    import backend.models.audit_log  # noqa: F401
    import backend.models.duplicate_dismissal  # noqa: F401
    import backend.models.kosync  # noqa: F401
    import backend.models.tome_sync  # noqa: F401
    import backend.models.opds_pin  # noqa: F401
    import backend.models.quick_connect  # noqa: F401

    Base.metadata.create_all(bind=test_engine)

    # Create the FTS virtual table (content-less FTS5 is a SQLite extension,
    # available in CPython's bundled sqlite3 build).
    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title, author, series, description, tags,
                content=''
            )
        """))
        conn.commit()


_init_test_db()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> Session:
    """Yields a SQLAlchemy Session that is rolled back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def admin_user(db: Session) -> tuple[User, str]:
    """Create an admin User with a UserPermission row and return (user, jwt_token)."""
    user = User(
        username="testadmin",
        email="testadmin@example.com",
        hashed_password=hash_password("adminpass123"),
        is_active=True,
        is_admin=True,
        must_change_password=False,
    )
    db.add(user)
    db.flush()  # populate user.id

    perms = UserPermission(
        user_id=user.id,
        can_upload=True,
        can_download=True,
        can_edit_metadata=True,
        can_delete_books=True,
        can_manage_libraries=True,
        can_manage_tags=True,
        can_manage_series=True,
        can_manage_users=True,
        can_approve_bindery=True,
        can_view_stats=True,
        can_use_opds=True,
        can_use_kosync=True,
        can_share=True,
        can_bulk_operations=True,
    )
    db.add(perms)
    db.flush()

    token = create_access_token(subject=user.id)
    return user, token


@pytest.fixture()
def client(db: Session, admin_user: tuple[User, str]) -> TestClient:
    """TestClient with the test DB injected and an admin Authorization header."""
    _user, token = admin_user

    # Import here so the app module is not initialised at collection time
    from backend.main import create_app

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def make_book(db: Session, admin_user: tuple[User, str]):
    """Factory fixture — call it to create a Book with sensible defaults.

    Accepts keyword overrides for any Book field plus:
      - ``file_path``  (default generated from title)
      - ``file_format`` (default "epub")
      - ``file_size``  (default 1024)
    """
    user, _ = admin_user
    created: list[Book] = []

    def _make(
        title: str = "Test Book",
        author: str | None = "Test Author",
        series: str | None = None,
        series_index: float | None = None,
        isbn: str | None = None,
        content_hash: str | None = None,
        year: int | None = None,
        subtitle: str | None = None,
        publisher: str | None = None,
        language: str | None = "en",
        description: str | None = None,
        cover_path: str | None = None,
        # BookFile fields
        file_path: str | None = None,
        file_format: str = "epub",
        file_size: int = 1024,
        tags: list[str] | None = None,
    ) -> Book:
        book = Book(
            title=title,
            subtitle=subtitle,
            author=author,
            series=series,
            series_index=series_index,
            isbn=isbn,
            content_hash=content_hash,
            year=year,
            publisher=publisher,
            language=language,
            description=description,
            cover_path=cover_path,
            status="active",
            added_by=user.id,
        )
        db.add(book)
        db.flush()  # populate book.id

        resolved_path = file_path or f"/library/{book.id}/{title.replace(' ', '_')}.{file_format}"
        bf = BookFile(
            book_id=book.id,
            file_path=resolved_path,
            format=file_format,
            file_size=file_size,
        )
        db.add(bf)

        for tag_str in (tags or []):
            db.add(BookTag(book_id=book.id, tag=tag_str, source="user"))

        db.flush()
        created.append(book)
        return book

    yield _make
