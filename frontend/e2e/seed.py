"""Seed scenarios for the Playwright E2E suite.

Runs against the sandbox instance started by e2e/start.sh (SQLite in WAL mode,
so writing from a second process while uvicorn is up is fine). Every scenario
starts from a clean slate: all book data, libraries and shelves wiped, library
dir recreated, the `e2e` admin and one member and one guest ensured.

Usage: seed.py <orphans|duplicates|many|race|shelves|sessions|stats|goals|goals-history|goal-entry|lengths|reset> [count]
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
from backend.models.library import Library, SavedFilter  # noqa: E402
from backend.models.tome_sync import ReadingSession  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.models.user_book_status import UserBookStatus  # noqa: E402

LIBRARY = E2E_DATA / "library"

ADMIN_USER = "e2e"
ADMIN_PASSWORD = "e2e-password-1"
MEMBER_USER = "e2e-member"
MEMBER_PASSWORD = "e2e-password-1"
GUEST_USER = "e2e-guest"
GUEST_PASSWORD = "e2e-password-1"


def reset(db) -> int:
    for table in (
        "share_links",
        "user_dashboards",
        "book_library",
        "library_users",
        "libraries",
        "saved_filters",
        "user_book_status",
        "book_tags",
        "book_files",
        "duplicate_dismissals",
        "books_fts",
        "reading_sessions",
        "reading_goal_targets",
        "reading_goals",
        "notifications",
        "books",
    ):
        db.execute(text(f"DELETE FROM {table}"))

    # The week start is the reader's own setting (Settings › Language and region): back to Monday.
    db.execute(text("UPDATE users SET week_start = 'monday'"))

    shutil.rmtree(LIBRARY, ignore_errors=True)
    LIBRARY.mkdir(parents=True)

    admin = _ensure_user(db, ADMIN_USER, ADMIN_PASSWORD, "admin")
    _ensure_user(db, MEMBER_USER, MEMBER_PASSWORD, "member")
    _ensure_user(db, GUEST_USER, GUEST_PASSWORD, "guest")
    return admin.id


def _ensure_user(db, username: str, password: str, role: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password(password),
            is_active=True,
            is_admin=role == "admin",
            role=role,
            must_change_password=False,
        )
        db.add(user)
        db.flush()
    return user


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


def scenario_shelves(db, admin_id):
    """One library and one shelf, for the sidebar sections."""
    db.add(Library(name="Seeded Library", owner_id=admin_id))
    db.add(SavedFilter(name="Seeded Shelf", owner_id=admin_id, params="{}"))


def scenario_sessions(db, admin_id):
    """Three books and 24 live reading sessions of the admin over the last two
    weeks, grouped unevenly by day (more than one page of the Sessions tab)."""
    from datetime import datetime, timedelta, timezone
    books = []
    for i in range(1, 4):
        b = _add_book(db, admin_id, f"Session Book {i}", author="Seed Author",
                      path=f"Sessions/Session Book {i}.epub", content=f"session {i}".encode(), fmt="epub")
        books.append(b)
    db.flush()
    today = datetime.now(timezone.utc).replace(tzinfo=None, hour=12, minute=0, second=0, microsecond=0)
    n = 0
    for day, count in enumerate([3, 1, 2, 1, 4, 1, 1, 2, 3, 1, 2, 1, 2]):
        for k in range(count):
            start = today - timedelta(days=day + 1) + timedelta(minutes=40 * k)
            secs = 600 + 300 * ((n * 7) % 5)
            db.add(ReadingSession(user_id=admin_id, book_id=books[n % 3].id, started_at=start,
                                  ended_at=start + timedelta(seconds=secs), duration_seconds=secs,
                                  pages_turned=10 + n, device="web"))
            n += 1


def _type_ids(db) -> dict[str, int]:
    """The default book types (seeded at startup), by slug."""
    from backend.models.library import BookType
    return {t.slug: t.id for t in db.query(BookType).all()}


def _status(db, admin_id, book, status, *, progress=None, finished_at=None, updated_at=None):
    from datetime import datetime
    db.add(UserBookStatus(user_id=admin_id, book_id=book.id, status=status, progress_pct=progress,
                          finished_at=finished_at, updated_at=updated_at or datetime.utcnow()))


def _sessions(db, admin_id, book, days_ago, *, minutes=20, pages=15):
    """One live session a day on each of `days_ago`, at noon UTC."""
    from datetime import datetime, timedelta
    noon = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    for d in days_ago:
        start = noon - timedelta(days=d)
        db.add(ReadingSession(user_id=admin_id, book_id=book.id, started_at=start,
                              ended_at=start + timedelta(minutes=minutes), duration_seconds=minutes * 60,
                              pages_turned=pages, device="web"))


def scenario_stats(db, admin_id, goals=False):
    """A reader for the Stats pages' paths the other scenarios lack:

    - Reading now: "Twin Format Novel" (one book, an EPUB and a PDF file),
      "Panel Comic" (CBZ) and "Almost Done" at 99.5 %, which offers Mark as read.
    - Two finished novels with sessions (a words-per-minute pace for the backlog).
    - Unstarted novels with word counts: two in "Seeded Library", three by
      "Shelf Author" (the "Short reads" shelf), one on Want to read, all with
      different word counts so every backlog scope has its own figure.
    - goals=True adds book-type-scoped year goals in each state (Manga reached,
      Comics ahead of pace, Light Novels behind pace) plus a weekly minutes goal,
      with the finished manga, comics and light novel those need.
    """
    import json
    from datetime import datetime, timedelta
    from backend.models.reading_goal import ReadingGoal
    types = _type_ids(db)
    now = datetime.utcnow()

    twin = _add_book(db, admin_id, "Twin Format Novel", author="Ada Twin",
                     path="Twin/Twin Format Novel.epub", content=b"twin epub bytes", fmt="epub")
    twin.book_type_id = types["book"]
    twin.word_count = 90_000
    _write_file("Twin/Twin Format Novel.pdf", b"twin pdf bytes")
    db.add(BookFile(book_id=twin.id, file_path=str(LIBRARY / "Twin/Twin Format Novel.pdf"),
                    format="pdf", file_size=len(b"twin pdf bytes")))
    comic = _add_book(db, admin_id, "Panel Comic", author="Cole Panel",
                      path="Comics/Panel Comic.cbz", content=b"panel comic bytes", fmt="cbz")
    comic.book_type_id = types["comic"]
    done = _add_book(db, admin_id, "Almost Done", author="Fin Ish",
                     path="Novels/Almost Done.epub", content=b"almost done bytes", fmt="epub")
    done.book_type_id = types["book"]
    done.word_count = 60_000
    # Updated in this order, so the most recent (the Home hero) is Almost Done.
    _status(db, admin_id, twin, "reading", progress=0.45, updated_at=now - timedelta(hours=3))
    _status(db, admin_id, comic, "reading", progress=0.30, updated_at=now - timedelta(hours=2))
    _status(db, admin_id, done, "reading", progress=0.995, updated_at=now - timedelta(hours=1))
    _sessions(db, admin_id, twin, [1, 3, 5, 8], minutes=30, pages=20)
    _sessions(db, admin_id, comic, [2, 4], minutes=10, pages=40)
    _sessions(db, admin_id, done, [1, 2, 6], minutes=25, pages=18)

    for i, wc in enumerate((80_000, 100_000), start=1):
        b = _add_book(db, admin_id, f"Finished Novel {i}", author="Fin Ish",
                      path=f"Novels/Finished Novel {i}.epub", content=f"finished {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        b.word_count = wc
        _status(db, admin_id, b, "read", progress=1.0, finished_at=now - timedelta(days=10 + i))
        _sessions(db, admin_id, b, [11 + i, 12 + i, 14 + i], minutes=40, pages=30)

    lib = Library(name="Seeded Library", owner_id=admin_id)
    db.add(lib)
    db.flush()
    for i, wc in enumerate((50_000, 70_000), start=1):
        b = _add_book(db, admin_id, f"Library Novel {i}", author="Lib Rary",
                      path=f"Library/Library Novel {i}.epub", content=f"library {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        b.word_count = wc
        lib.books.append(b)
    for i, wc in enumerate((20_000, 25_000, 30_000), start=1):
        b = _add_book(db, admin_id, f"Short Read {i}", author="Shelf Author",
                      path=f"Short/Short Read {i}.epub", content=f"short {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        b.word_count = wc
    want = _add_book(db, admin_id, "Wanted Novel", author="Will Want",
                     path="Novels/Wanted Novel.epub", content=b"wanted bytes", fmt="epub")
    want.book_type_id = types["book"]
    want.word_count = 150_000
    _status(db, admin_id, want, "want_to_read")
    db.add(SavedFilter(name="Short reads", owner_id=admin_id, params=json.dumps({"author": "Shelf Author"})))

    if not goals:
        return
    # This year's elapsed share, as the server paces a year goal (local calendar year).
    local = datetime.now()
    y0, y1 = datetime(local.year, 1, 1), datetime(local.year + 1, 1, 1)
    f = (local - y0).total_seconds() / (y1 - y0).total_seconds()
    # Comics ahead of pace and not reached: target = c + 1 with c - (c + 1) * f >= 1.5.
    ahead = int((1.5 + f) / (1 - f)) + 1
    finished = {"manga": 2, "comic": ahead, "light_novel": 1}
    k = 0
    for slug, n in finished.items():
        for i in range(1, n + 1):
            k += 1
            b = _add_book(db, admin_id, f"Goal {slug} {i:03d}", author="Goal Author",
                          path=f"Goals/{slug}/{i:03d}.cbz", content=f"goal {slug} {i}".encode(), fmt="cbz")
            b.book_type_id = types[slug]
            _status(db, admin_id, b, "read", progress=1.0, finished_at=now - timedelta(minutes=5 + k))
    db.flush()
    t0 = now - timedelta(days=1)
    for j, (kind, target, slug) in enumerate((
        ("books_per_year", 2, "manga"),
        ("books_per_year", ahead + 1, "comic"),
        ("books_per_year", 40, "light_novel"),
        ("minutes_per_week", 300, None),
        ("days_per_week", 3, None),
    )):
        db.add(ReadingGoal(user_id=admin_id, kind=kind, target=target,
                           book_type_id=types[slug] if slug else None,
                           created_at=t0 + timedelta(seconds=j), updated_at=t0))
    # Finish by a date: "Done Saga" (both volumes read: completed) by the end of next month.
    for i in (1, 2):
        b = _add_book(db, admin_id, f"Done Saga {i}", author="Saga Author", series="Done Saga",
                      series_index=float(i), path=f"Saga/Done Saga {i}.epub", content=f"done saga {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        _status(db, admin_id, b, "read", progress=1.0, finished_at=now - timedelta(days=3 + i))
    due = (datetime(local.year + (local.month == 12), local.month % 12 + 1, 28)).date()
    db.add(ReadingGoal(user_id=admin_id, kind="finish_by", target=2, target_series="Done Saga", due_date=due,
                       created_at=t0 + timedelta(seconds=9), updated_at=t0))


def scenario_goals_history(db, admin_id):
    """A reader with three months of reading (40-80 minutes on most days) and three
    timed finished books, so the goal dialog suggests targets from their own days."""
    from datetime import datetime, timedelta
    types = _type_ids(db)
    now = datetime.utcnow()
    daily = _add_book(db, admin_id, "Daily Novel", author="Day Reader",
                      path="Novels/Daily Novel.epub", content=b"daily bytes", fmt="epub")
    daily.book_type_id = types["book"]
    _status(db, admin_id, daily, "reading", progress=0.4, updated_at=now - timedelta(hours=2))
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    for d in range(1, 91):
        if d % 7 == 3:
            continue  # a day off a week
        minutes = 40 + (d * 13) % 41
        start = noon - timedelta(days=d)
        db.add(ReadingSession(user_id=admin_id, book_id=daily.id, started_at=start,
                              ended_at=start + timedelta(minutes=minutes), duration_seconds=minutes * 60,
                              pages_turned=minutes, device="web"))
    for i in range(1, 4):
        b = _add_book(db, admin_id, f"Timed Novel {i}", author="Day Reader",
                      path=f"Novels/Timed Novel {i}.epub", content=f"timed {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        _status(db, admin_id, b, "read", progress=1.0, finished_at=now - timedelta(days=100 + i))
        start = noon - timedelta(days=110 + i)
        db.add(ReadingSession(user_id=admin_id, book_id=b.id, started_at=start,
                              ended_at=start + timedelta(minutes=300), duration_seconds=300 * 60,
                              pages_turned=300, device="web"))


def scenario_goal_entry(db, admin_id):
    """The `stats` reader plus what the ways into a finish-by goal need: "Long Road",
    a series of three with the first volume in progress (a series and a book to
    finish), and "Read Saga", a series of two already read (nothing left to finish)."""
    from datetime import datetime, timedelta
    scenario_stats(db, admin_id)
    types = _type_ids(db)
    now = datetime.utcnow()
    for i, wc in enumerate((60_000, 70_000, 80_000), start=1):
        b = _add_book(db, admin_id, f"Long Road {i}", author="Road Author", series="Long Road",
                      series_index=float(i), path=f"Road/Long Road {i}.epub", content=f"long road {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        b.word_count = wc
        if i == 1:
            _status(db, admin_id, b, "reading", progress=0.3, updated_at=now - timedelta(hours=5))
            _sessions(db, admin_id, b, [2, 3], minutes=30, pages=20)
    for i in (1, 2):
        b = _add_book(db, admin_id, f"Read Saga {i}", author="Saga Author", series="Read Saga",
                      series_index=float(i), path=f"ReadSaga/Read Saga {i}.epub", content=f"read saga {i}".encode(), fmt="epub")
        b.book_type_id = types["book"]
        _status(db, admin_id, b, "read", progress=1.0, finished_at=now - timedelta(days=20 + i))


def scenario_lengths(db, admin_id):
    for title, words in (("Short", 20000), ("Medium", 50000), ("Long", 120000), ("Unknown", None)):
        _add_book(db, admin_id, title, path=f"Lengths/{title}.epub", fmt="epub").word_count = words


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
        elif scenario == "shelves":
            scenario_shelves(db, admin_id)
        elif scenario == "sessions":
            scenario_sessions(db, admin_id)
        elif scenario == "stats":
            scenario_stats(db, admin_id)
        elif scenario == "goals":
            scenario_stats(db, admin_id, goals=True)
        elif scenario == "goals-history":
            scenario_goals_history(db, admin_id)
        elif scenario == "goal-entry":
            scenario_goal_entry(db, admin_id)
        elif scenario == "lengths":
            scenario_lengths(db, admin_id)
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
