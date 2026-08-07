"""Seed scenarios for the Playwright E2E suite.

Runs against the sandbox instance started by e2e/start.sh (SQLite in WAL mode,
so writing from a second process while uvicorn is up is fine). Every scenario
starts from a clean slate: all book data wiped, library dir recreated, the
`e2e` admin ensured.

Usage: seed.py <orphans|duplicates|many> [count]
"""
import sys
from pathlib import Path

E2E_DATA = Path(__file__).resolve().parent / ".data"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os  # noqa: E402
os.environ["TOME_SECRET_KEY"] = "e2e-secret"
os.environ["TOME_DATA_DIR"] = str(E2E_DATA / "data")
os.environ["TOME_LIBRARY_DIR"] = str(E2E_DATA / "library")
os.environ["TOME_INCOMING_DIR"] = str(E2E_DATA / "bindery")

import hashlib  # noqa: E402
import shutil  # noqa: E402

from sqlalchemy import text  # noqa: E402

from backend.core.database import SessionLocal  # noqa: E402
from backend.core.security import hash_password  # noqa: E402
from backend.models.book import Book, BookFile  # noqa: E402
from backend.models.user import User  # noqa: E402

LIBRARY = E2E_DATA / "library"

ADMIN_USER = "e2e"
ADMIN_PASSWORD = "e2e-password-1"


def reset(db) -> int:
    for table in (
        "user_book_status",
        "book_tags",
        "book_files",
        "duplicate_dismissals",
        "books_fts",
        "books",
    ):
        db.execute(text(f"DELETE FROM {table}"))

    shutil.rmtree(LIBRARY, ignore_errors=True)
    LIBRARY.mkdir(parents=True)

    admin = db.query(User).filter(User.username == ADMIN_USER).first()
    if admin is None:
        admin = User(
            username=ADMIN_USER,
            email="e2e@example.com",
            hashed_password=hash_password(ADMIN_PASSWORD),
            is_active=True,
            is_admin=True,
            role="admin",
            must_change_password=False,
        )
        db.add(admin)
        db.flush()
    return admin.id


def _write_file(rel_path: str, content: bytes) -> Path:
    p = LIBRARY / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _add_book(db, admin_id, title, *, author=None, series=None, series_index=None,
              path=None, content=None, dead=False, isbn=None, content_hash=None,
              fmt="cbz"):
    """Create a Book + one BookFile. `dead=True` records a path that does not
    exist on disk; otherwise the file is written with `content`."""
    if content_hash is None and content is not None:
        content_hash = hashlib.sha256(content).hexdigest()
    book = Book(title=title, author=author, series=series, series_index=series_index,
                status="active", added_by=admin_id, content_hash=content_hash, isbn=isbn)
    db.add(book)
    db.flush()
    abs_path = LIBRARY / path
    if not dead:
        _write_file(path, content or title.encode())
    db.add(BookFile(book_id=book.id, file_path=str(abs_path), format=fmt,
                    file_size=(abs_path.stat().st_size if not dead else 11111)))
    db.flush()
    return book


def scenario_orphans(db, admin_id):
    """Pablo's situation (#165): hash-identical re-imports whose originals were
    deleted from disk, one fully dead book, one book with a dead extra file."""
    op1 = b"one piece vol 1 bytes"
    op2 = b"one piece vol 2 bytes"
    _add_book(db, admin_id, "One Piece Vol. 1", author="Eiichiro Oda", series="One Piece",
              series_index=1, path="One Piece/One Piece Vol. 1.cbz", content=op1)
    _add_book(db, admin_id, "one_piece_v01[scan]", author="Eiichiro Oda", series="One Piece",
              series_index=1, path="One Piece/one_piece_v01[scan].cbz", dead=True,
              content_hash=hashlib.sha256(op1).hexdigest())
    _add_book(db, admin_id, "One Piece Vol. 2", author="Eiichiro Oda", series="One Piece",
              series_index=2, path="One Piece/One Piece Vol. 2.cbz", content=op2)
    _add_book(db, admin_id, "one_piece_v02[scan]", author="Eiichiro Oda", series="One Piece",
              series_index=2, path="One Piece/one_piece_v02[scan].cbz", dead=True,
              content_hash=hashlib.sha256(op2).hexdigest())
    _add_book(db, admin_id, "Bleach Vol. 1", author="Tite Kubo", series="Bleach",
              series_index=1, path="Bleach/bleach_v01.cbz", dead=True, content_hash="b" * 64)
    aot = _add_book(db, admin_id, "Attack on Titan Vol. 1", author="Hajime Isayama",
                    series="Attack on Titan", series_index=1,
                    path="Attack on Titan/Attack on Titan Vol. 1.cbz",
                    content=b"aot vol 1 bytes")
    db.add(BookFile(book_id=aot.id, file_path=str(LIBRARY / "Attack on Titan/aot_v01_old.pdf"),
                    format="pdf", file_size=999))
    db.flush()


def scenario_duplicates(db, admin_id):
    """Three resolvable groups: a healthy hash pair (merge), a hash pair with a
    dead copy (delete others), and an ISBN pair (dismiss). The Naruto pair also
    surfaces as a same-series-volume group — the overlap the failure-path test
    relies on."""
    naruto = b"naruto vol 1 bytes"
    op1 = b"one piece vol 1 bytes"
    _add_book(db, admin_id, "Naruto Vol. 1", author="Masashi Kishimoto", series="Naruto",
              series_index=1, path="Naruto/Naruto Vol. 1.cbz", content=naruto)
    _add_book(db, admin_id, "Naruto Vol. 1 (copy)", author="Masashi Kishimoto", series="Naruto",
              series_index=1, path="Naruto/Naruto Vol. 1 (copy).cbz", content=b"naruto copy bytes",
              content_hash=hashlib.sha256(naruto).hexdigest())
    _add_book(db, admin_id, "One Piece Vol. 1", author="Eiichiro Oda", series="One Piece",
              series_index=1, path="One Piece/One Piece Vol. 1.cbz", content=op1)
    _add_book(db, admin_id, "one_piece_v01[scan]", author="Eiichiro Oda", series="One Piece",
              series_index=1, path="One Piece/one_piece_v01[scan].cbz", dead=True,
              content_hash=hashlib.sha256(op1).hexdigest())
    _add_book(db, admin_id, "Bleach Vol. 1", author="Tite Kubo", series="Bleach",
              series_index=1, path="Bleach/bleach_v01.cbz", content=b"bleach bytes",
              isbn="978-1-56931-441-3")
    _add_book(db, admin_id, "Bleach Vol. 01 Omnibus", author="Tite Kubo", series="Bleach",
              series_index=1.5, path="Bleach/bleach_v01_omnibus.cbz", content=b"omnibus bytes",
              isbn="978-1-56931-441-3")


def scenario_race(db, admin_id):
    """Data where the grouped and flat orderings genuinely differ, so a stale
    page from one view leaks books the other view loads again later (the
    group/ungroup viewport-duplicates race). Flat order: 40 Alpha volumes,
    80 solos, 40 Zeta volumes. Grouped order: Alpha stack, 80 solos, Zeta
    stack — page boundaries land on different books in each view."""
    for i in range(1, 41):
        _add_book(db, admin_id, f"Alpha Saga Vol {i:02d}", author="Race Author",
                  series="Alpha Saga", series_index=float(i),
                  path=f"Alpha/Alpha {i:02d}.epub", content=f"alpha {i}".encode(), fmt="epub")
    for i in range(1, 81):
        _add_book(db, admin_id, f"Middle Solo {i:03d}", author="Race Author",
                  path=f"Middle/Middle {i:03d}.epub", content=f"middle {i}".encode(), fmt="epub")
    for i in range(1, 41):
        _add_book(db, admin_id, f"Zeta Saga Vol {i:02d}", author="Race Author",
                  series="Zeta Saga", series_index=float(i),
                  path=f"Zeta/Zeta {i:02d}.epub", content=f"zeta {i}".encode(), fmt="epub")


def scenario_many(db, admin_id, count):
    """`count` distinct books for pagination / select-all / bulk tests."""
    for i in range(1, count + 1):
        _add_book(db, admin_id, f"Seeded Book {i:03d}", author="Seed Author",
                  series="Seeded Series" if i % 2 == 0 else None,
                  series_index=float(i) if i % 2 == 0 else None,
                  path=f"Seeded/Seeded Book {i:03d}.epub",
                  content=f"seeded {i}".encode(), fmt="epub")


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "orphans"
    db = SessionLocal()
    try:
        admin_id = reset(db)
        if scenario == "orphans":
            scenario_orphans(db, admin_id)
        elif scenario == "duplicates":
            scenario_duplicates(db, admin_id)
        elif scenario == "race":
            scenario_race(db, admin_id)
        elif scenario == "many":
            scenario_many(db, admin_id, int(sys.argv[2]) if len(sys.argv) > 2 else 134)
        elif scenario == "reset":
            pass
        else:
            raise SystemExit(f"unknown scenario: {scenario}")
        db.commit()
        print(f"seeded scenario={scenario} books={db.query(Book).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
