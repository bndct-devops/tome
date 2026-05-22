"""Build the showcase database used for marketing screenshots.

Run from the repo root:

    python scripts/seed_showcase.py

Creates:
    data/showcase/tome.db        Fresh SQLite, seeded.
    data/showcase/covers/        Cover JPEGs (copied from docs/seed/covers/)
    library/showcase/            Empty (placeholder paths only — no real ebook files)

Then point a Tome backend at this dir:

    TOME_SECRET_KEY=dev TOME_DATA_DIR=./data/showcase \
      TOME_LIBRARY_DIR=./library/showcase TOME_INCOMING_DIR=./bindery/showcase \
      python -m uvicorn backend.main:app --port 8090 --reload

The seed is deterministic — random.seed is fixed, so re-running produces
identical data. Idempotent: drops + recreates the DB on every run.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOWCASE_DIR = REPO_ROOT / "data" / "showcase"
SHOWCASE_COVERS = SHOWCASE_DIR / "covers"
SHOWCASE_LIBRARY = REPO_ROOT / "library" / "showcase"
SHOWCASE_BINDERY = REPO_ROOT / "bindery" / "showcase"
SEED_COVERS = REPO_ROOT / "docs" / "seed" / "covers"
SEED_LIBRARY = REPO_ROOT / "docs" / "seed" / "library"

# Set env BEFORE importing backend so settings picks them up
os.environ.setdefault("TOME_SECRET_KEY", "dev")
os.environ["TOME_DATA_DIR"] = str(SHOWCASE_DIR)
os.environ["TOME_LIBRARY_DIR"] = str(SHOWCASE_LIBRARY)
os.environ["TOME_INCOMING_DIR"] = str(SHOWCASE_BINDERY)

sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import models AFTER env is set
from backend.core.database import Base  # noqa: E402
from backend.core.security import hash_password  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.models.book import Book, BookFile, BookTag  # noqa: E402
from backend.models.library import BookType, Library, SavedFilter  # noqa: E402
from backend.models.user_book_status import UserBookStatus  # noqa: E402
from backend.models.tome_sync import ReadingSession, TomeSyncPosition  # noqa: E402
from backend.models.series_meta import Arc, SeriesMeta  # noqa: E402

# Import remaining models so create_all picks them up
import backend.models.audit_log  # noqa: E402,F401
import backend.models.duplicate_dismissal  # noqa: E402,F401
import backend.models.kosync  # noqa: E402,F401
import backend.models.opds_pin  # noqa: E402,F401
import backend.models.quick_connect  # noqa: E402,F401
import backend.models.api_token  # noqa: E402,F401


# ── Data ──────────────────────────────────────────────────────────────────────

# Each book: (cover_slug, title, author, series, series_index, year, format, book_type_slug)
# Series descriptions — applied to every book in that series so series-detail
# headers render properly. Single-book descriptions live in BOOKS below.
SERIES_DESCRIPTIONS = {
    "Berserk": "Created by Kentaro Miura, Berserk is manga mayhem to the extreme — violent, horrifying, and mercilessly funny — and the wellspring for the internationally popular anime series. His name is Guts, the Black Swordsman, a feared warrior spoken of only in whispers, bearing a gigantic sword, an iron hand, and the scars of countless battles.",
    "Vinland Saga": "A young Viking on a hellbent quest for revenge against the warrior who killed his father. Makoto Yukimura's epic plunge into a brutal age, where loyalty and friendship can be bought with a sword, and where the price of revenge is steeper than expected.",
    "Frieren: Beyond Journey's End": "Decades after a hero's party defeats the Demon King, the immortal elf mage Frieren sets out to learn what she missed about her short-lived companions. A quiet, mournful adventure about time, memory, and what it means to live.",
    "One Piece": "Monkey D. Luffy refuses to let anyone or anything stand in the way of his quest to become King of the Pirates. With a course charted for the Grand Line and an unstoppable crew, Luffy seeks the legendary treasure: One Piece.",
    "The Good Guys": "Clyde Hatchett is having the worst day of his life — until an oddball offer lands him as Montana the larger-than-life tank warrior inside iNcarn8, a game claiming to be a whole new life. Eric Ugland's flagship LitRPG series.",
    "The Bad Guys": "Same world, different side. The Bad Guys follows the rogues, scoundrels and scoundrel-adjacent of the iNcarn8 universe. Heists, betrayals, and questionable life choices — all delivered with Ugland's signature dry wit.",
}

# Book entries: (slug, title, author, series, series_index, year, format, book_type_slug, description)
# Standalone descriptions for non-series books.
BOOKS = [
    # Western fiction
    ("project-hail-mary",  "Project Hail Mary",                "Andy Weir",        None, None, 2021, "epub", "novel",
     "Ryland Grace wakes up alone on a spaceship millions of miles from home, with no memory of how he got there or what he's supposed to do. As the puzzle pieces come back, he discovers he's humanity's last chance to stop an extinction-level threat — and that he's not alone in the void."),
    ("dune",               "Dune",                              "Frank Herbert",    None, None, 1965, "epub", "novel",
     "Set on the desert planet Arrakis, Dune is the story of the boy Paul Atreides, heir to a noble family tasked with ruling an inhospitable world where the only thing of value is the spice melange — used for travel, prolonging life, and prescience. A blend of politics, religion, ecology and adventure that defined modern science fiction."),
    ("ready-player-one",   "Ready Player One",                  "Ernest Cline",     None, None, 2011, "epub", "novel",
     "In 2045, reality is an ugly place. The only time Wade Watts truly feels alive is when he's jacked into the OASIS, a virtual utopia. When the OASIS' eccentric creator dies and leaves behind a treasure hunt, Wade joins a global contest for unimaginable wealth — and the future of humanity."),
    ("hitchhikers-guide",  "The Hitchhiker's Guide to the Galaxy", "Douglas Adams", None, None, 1979, "epub", "novel",
     "Arthur Dent's house is about to be demolished. So is the planet Earth, to make way for a hyperspace bypass. What follows is a wildly funny interstellar journey featuring Vogons, a depressed robot, a two-headed galactic president, and the answer to life, the universe and everything (it's 42)."),

    # Berserk (10 vols across 2 arcs)
    ("berserk-01", "Berserk, Vol. 1", "Kentaro Miura", "Berserk", 1, 1990, "cbz", "manga", None),
    ("berserk-02", "Berserk, Vol. 2", "Kentaro Miura", "Berserk", 2, 1991, "cbz", "manga", None),
    ("berserk-03", "Berserk, Vol. 3", "Kentaro Miura", "Berserk", 3, 1991, "cbz", "manga", None),
    ("berserk-04", "Berserk, Vol. 4", "Kentaro Miura", "Berserk", 4, 1992, "cbz", "manga", None),
    ("berserk-05", "Berserk, Vol. 5", "Kentaro Miura", "Berserk", 5, 1992, "cbz", "manga", None),
    ("berserk-06", "Berserk, Vol. 6", "Kentaro Miura", "Berserk", 6, 1993, "cbz", "manga", None),
    ("berserk-07", "Berserk, Vol. 7", "Kentaro Miura", "Berserk", 7, 1993, "cbz", "manga", None),
    ("berserk-08", "Berserk, Vol. 8", "Kentaro Miura", "Berserk", 8, 1994, "cbz", "manga", None),
    ("berserk-09", "Berserk, Vol. 9", "Kentaro Miura", "Berserk", 9, 1994, "cbz", "manga", None),
    ("berserk-10", "Berserk, Vol. 10","Kentaro Miura", "Berserk", 10, 1995, "cbz", "manga", None),

    # Vinland Saga (3 omnibus)
    ("vinland-saga-01", "Vinland Saga Omnibus, Vol. 1", "Makoto Yukimura", "Vinland Saga", 1, 2013, "cbz", "manga", None),
    ("vinland-saga-02", "Vinland Saga Omnibus, Vol. 2", "Makoto Yukimura", "Vinland Saga", 2, 2014, "cbz", "manga", None),
    ("vinland-saga-03", "Vinland Saga Omnibus, Vol. 3", "Makoto Yukimura", "Vinland Saga", 3, 2014, "cbz", "manga", None),

    # Frieren
    ("frieren-01", "Frieren: Beyond Journey's End, Vol. 1", "Kanehito Yamada", "Frieren: Beyond Journey's End", 1, 2021, "cbz", "manga", None),
    ("frieren-02", "Frieren: Beyond Journey's End, Vol. 2", "Kanehito Yamada", "Frieren: Beyond Journey's End", 2, 2021, "cbz", "manga", None),
    ("frieren-03", "Frieren: Beyond Journey's End, Vol. 3", "Kanehito Yamada", "Frieren: Beyond Journey's End", 3, 2021, "cbz", "manga", None),

    # One Piece (10 vols)
    ("one-piece-01",  "One Piece, Vol. 1: Romance Dawn",         "Eiichiro Oda", "One Piece", 1,  1997, "cbz", "manga", None),
    ("one-piece-02",  "One Piece, Vol. 2: Buggy the Clown",      "Eiichiro Oda", "One Piece", 2,  1998, "cbz", "manga", None),
    ("one-piece-03",  "One Piece, Vol. 3: Don't Get Fooled Again","Eiichiro Oda", "One Piece", 3, 1998, "cbz", "manga", None),
    ("one-piece-04",  "One Piece, Vol. 4: The Black Cat Pirates","Eiichiro Oda", "One Piece", 4,  1998, "cbz", "manga", None),
    ("one-piece-05",  "One Piece, Vol. 5: For Whom the Bell Tolls","Eiichiro Oda", "One Piece", 5, 1999, "cbz", "manga", None),
    ("one-piece-06",  "One Piece, Vol. 6: The Oath",             "Eiichiro Oda", "One Piece", 6,  1999, "cbz", "manga", None),
    ("one-piece-07",  "One Piece, Vol. 7: The Crap-Geezer",      "Eiichiro Oda", "One Piece", 7,  1999, "cbz", "manga", None),
    ("one-piece-08",  "One Piece, Vol. 8: I Won't Die",          "Eiichiro Oda", "One Piece", 8,  1999, "cbz", "manga", None),
    ("one-piece-09",  "One Piece, Vol. 9: Tears",                "Eiichiro Oda", "One Piece", 9,  1999, "cbz", "manga", None),
    ("one-piece-10",  "One Piece, Vol. 10: OK, Let's Stand Up!", "Eiichiro Oda", "One Piece", 10, 1999, "cbz", "manga", None),

    # The Good Guys (Eric Ugland)
    ("good-guys-01", "One More Last Time",     "Eric Ugland", "The Good Guys", 1, 2018, "epub", "novel", None),
    ("good-guys-02", "Heir Today, Pawn Tomorrow", "Eric Ugland", "The Good Guys", 2, 2018, "epub", "novel", None),
    ("good-guys-03", "Dungeon Mauling",        "Eric Ugland", "The Good Guys", 3, 2019, "epub", "novel", None),

    # The Bad Guys (Eric Ugland) — incl. Skull and Thrones at vol 3
    ("bad-guys-01", "Scamps & Scoundrels",  "Eric Ugland", "The Bad Guys", 1, 2019, "epub", "novel", None),
    ("bad-guys-02", "Second Story Man",     "Eric Ugland", "The Bad Guys", 2, 2020, "epub", "novel", None),
    ("bad-guys-03", "Skull and Thrones",    "Eric Ugland", "The Bad Guys", 3, 2020, "epub", "novel", None),

    # Classic — has a real EPUB at docs/seed/library/frankenstein.epub for the reader screenshot
    ("frankenstein",       "Frankenstein",                      "Mary Shelley",     None, None, 1818, "epub", "novel",
     "The first true science fiction novel. A young scientist creates a sapient creature in an unorthodox experiment — and discovers what it means to play god, to grieve, and to be utterly responsible for what you make."),

    # Comic first-issues
    ("action-comics-01",   "Action Comics #1",      "Jerry Siegel & Joe Shuster",        None, None, 1938, "cbz", "comic",
     "The debut of Superman. Jerry Siegel and Joe Shuster's first appearance of the Man of Steel — the comic that launched the entire superhero genre."),
    ("amazing-fantasy-15", "Amazing Fantasy #15",   "Stan Lee & Steve Ditko",            None, None, 1962, "cbz", "comic",
     "The first appearance of Spider-Man. Stan Lee and Steve Ditko introduce Peter Parker, the bookish high-school student whose life changes after a radioactive spider bite — and a single phrase: with great power comes great responsibility."),
    ("detective-comics-27","Detective Comics #27",  "Bob Kane & Bill Finger",            None, None, 1939, "cbz", "comic",
     "The debut of the Batman. Bob Kane and Bill Finger's six-page mystery introduces the caped vigilante of Gotham City in a story called 'The Case of the Chemical Syndicate'."),
    ("watchmen-01",        "Watchmen #1",            "Alan Moore & Dave Gibbons",         None, None, 1986, "cbz", "comic",
     "Alan Moore and Dave Gibbons' genre-defining deconstruction of the superhero. A retired vigilante is murdered, and his former colleagues — washed-up, traumatised, half-mad — start to unravel a conspiracy. The book that proved comics could be literature."),
]

# Series extensions — placeholder books (vol N+) inserted in addition to BOOKS
# so the series-detail header shows realistic total counts (e.g. "Book 3 of 16")
# even though we only seed covers for vols 1-3. These rows have no cover, no
# BookFile — they're library metadata only.
SERIES_EXTENSIONS: dict[str, list[tuple[int, str]]] = {
    "The Good Guys": [
        (4, "Four: The Loot"), (5, "Dukes and Ladders"), (6, "Home, Siege Home"),
        (7, "The Bare Hunt"), (8, "Eastbound and Town"), (9, "Four Beheadings and a Funeral"),
        (10, "Eat, Slay, Love"), (11, "Killing Them Awfully"), (12, "Wild Wild Quest"),
        (13, "Flex in the City"), (14, "Of Slicing Men"), (15, "Bad to the Throne"),
        (16, "One Man's Laughter"),
    ],
    "The Bad Guys": [
        (4, "War of the Posers"), (5, "Seas the Day"), (6, "High Gloom"),
        (7, "Back to One"), (8, "Trick of the Night"), (9, "Darktown Funk"),
        (10, "On a Throne of Lies"), (11, "2 Lies, 2 Thrones"),
    ],
}

# Series-level metadata (status badge in series view)
SERIES_STATUS = {
    "Berserk":                       "hiatus",      # Miura passed; assistants continuing
    "Vinland Saga":                  "completed",
    "Frieren: Beyond Journey's End": "ongoing",
    "One Piece":                     "ongoing",
    "The Good Guys":                 "ongoing",
    "The Bad Guys":                  "ongoing",
}

# Berserk arcs covering the seeded volumes 1-10
ARCS = [
    ("Berserk", "Black Swordsman", 1.0, 3.0, "A scarred mercenary hunts apostles across a plague-ridden land, driven by a branded mark that draws evil spirits nightly."),
    ("Berserk", "Golden Age",      3.0, 14.0, "Flashback to young Guts joining Griffith's Band of the Hawk, their rise through a hundred-year war, and the catastrophic Eclipse betrayal."),
]

USER = {
    "username": "benedict",
    "email": "benedict@example.com",
    "password": "showcase",  # never used; this DB is local-only for screenshots
    "is_admin": True,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def reset_dirs():
    """Drop and recreate showcase dirs."""
    for d in (SHOWCASE_DIR, SHOWCASE_LIBRARY, SHOWCASE_BINDERY):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    SHOWCASE_COVERS.mkdir(parents=True, exist_ok=True)


def copy_cover(slug: str, content_hash: str) -> str | None:
    """Copy a cover from the seed dir into the showcase covers dir.

    Returns the relative filename (matches how Tome stores it).
    """
    src = SEED_COVERS / f"{slug}.jpg"
    if not src.exists():
        print(f"  ! missing cover: {src}", file=sys.stderr)
        return None
    dst_name = f"{content_hash[:16]}.jpg"
    dst = SHOWCASE_COVERS / dst_name
    shutil.copy2(src, dst)
    return dst_name


def fake_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def init_db():
    """Create a fresh SQLite at showcase/tome.db and apply schema."""
    db_path = SHOWCASE_DIR / "tome.db"
    if db_path.exists():
        db_path.unlink()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


def seed_book_types(db):
    """Insert the default book type rows + their auto-libraries."""
    types = [
        ("novel", "Books",        "BookOpen",  "blue",   1),
        ("manga", "Manga",        "Layers",    "pink",   2),
        ("comic", "Comics",       "BookMarked","purple", 3),
        ("ln",    "Light Novels", "Library",   "orange", 4),
    ]
    type_by_slug: dict[str, BookType] = {}
    for slug, label, icon, color, sort_order in types:
        lib = Library(name=label, icon=icon, is_public=True, owner_id=None, sort_order=sort_order)
        db.add(lib)
        db.flush()
        bt = BookType(slug=slug, label=label, icon=icon, color=color, library_id=lib.id, sort_order=sort_order)
        db.add(bt)
        db.flush()
        type_by_slug[slug] = bt
    return type_by_slug


# ── Reading-session generator ─────────────────────────────────────────────────

def generate_sessions(
    db,
    books_by_slug: dict[str, Book],
    user_id: int,
) -> tuple[list[ReadingSession], dict[str, UserBookStatus], dict[str, TomeSyncPosition]]:
    """Return (sessions, statuses, positions) emulating a year of reading.

    Day-driven generator. For each of the last 365 days:
      - Decide if the day is active (weighted by recency + current streak)
      - For the recent N=47 days, force at least one session (the streak)
      - 1-3 sessions per active day, weighted toward short evening reads
      - Pick a book from the current "rotation" — books that are currently
        being read at that point in time
      - Weekend bias for longer / binge sessions
      - One outlier weekend with 4-5h on a single book
    """
    rng = random.Random(42)  # deterministic
    today = date.today()
    devices_for_format = {
        "epub": ["Kindle Paperwhite", "Kobo Libra Colour", "web"],
        "cbz":  ["Kobo Libra Colour", "Kindle Scribe", "web"],
        "comic":["Kobo Libra Colour", "web"],
    }

    # ── Book lifecycle: when was each book in rotation? ─────────────────────
    # Currently reading — Dungeon Mauling (Good Guys 3) + Project Hail Mary
    currently_reading_slugs = ["good-guys-03", "project-hail-mary"]

    # (slug, days_ago_started, days_ago_finished or None, target_state)
    # — `reading`: currently active, no end date
    # — `read`:    finished in the past; sessions clustered in the lifecycle window
    # — `stalled`: small batch of sessions long ago, no status record (won't show in Currently Reading)
    lifecycle = [
        # Currently reading
        ("good-guys-03",      25,   None, "reading"),
        ("project-hail-mary",  18,   None, "reading"),
        # Recently finished (in last 30 days) — drives the headline "books finished (30d)"
        ("bad-guys-03",        45,   8, "read"),     # Skull and Thrones — just finished
        ("vinland-saga-03",    55,  14, "read"),     # finished mid-month
        ("berserk-04",         30,  22, "read"),     # finished ~3 weeks ago
        ("berserk-05",         24,  16, "read"),     # finished ~2.5 weeks ago
        # Older finishes (35-300 days ago)
        ("ready-player-one",  330, 305, "read"),
        ("hitchhikers-guide", 295, 270, "read"),
        ("dune",              260, 225, "read"),
        ("good-guys-01",      215, 190, "read"),
        ("good-guys-02",      188, 165, "read"),
        ("bad-guys-01",       160, 138, "read"),
        ("bad-guys-02",       135, 110, "read"),
        ("vinland-saga-01",   240, 218, "read"),
        ("vinland-saga-02",   215, 195, "read"),
        ("berserk-01",         80,  68, "read"),    # the binge weekend lives here
        ("berserk-02",         67,  60, "read"),
        ("berserk-03",         59,  47, "read"),
        # Stalled — quick taste long ago, never picked up again. NO status record.
        ("one-piece-01",      340, 320, "stalled"),
        ("one-piece-02",      300, 285, "stalled"),
        ("frieren-01",         85,  82, "stalled"),
    ]

    # Build a per-date list of available "in rotation" books
    rotation_by_date: dict[date, list[str]] = {}
    for slug, start_ago, end_ago, _ in lifecycle:
        end_ago = end_ago if end_ago is not None else 0
        for off in range(end_ago, start_ago + 1):
            d = today - timedelta(days=off)
            rotation_by_date.setdefault(d, []).append(slug)

    # ── Day-driven session generation ───────────────────────────────────────
    sessions: list[ReadingSession] = []
    STREAK_LEN = 47  # last 47 days will all have ≥1 session

    for days_ago in range(365):
        d = today - timedelta(days=days_ago)
        is_weekend = d.weekday() >= 5
        in_streak = days_ago < STREAK_LEN

        # Probability of having a session today
        if in_streak:
            active = True
        else:
            base_p = 0.78 if is_weekend else 0.68
            active = rng.random() < base_p

        if not active:
            continue

        # How many sessions today
        if is_weekend:
            n_sessions = rng.choices([1, 2, 3, 4], weights=[0.35, 0.35, 0.20, 0.10])[0]
        else:
            n_sessions = rng.choices([1, 2, 3], weights=[0.55, 0.35, 0.10])[0]

        # Pick available books from rotation. If no rotation on this date, use any currently-reading
        avail = rotation_by_date.get(d) or currently_reading_slugs

        for _ in range(n_sessions):
            slug = rng.choice(avail)
            book = books_by_slug[slug]
            fmt = "cbz" if book.book_type_id and "manga" in str(book.book_type_id) else "epub"
            devices = devices_for_format.get(fmt, ["Kindle Paperwhite", "Kobo Libra Colour", "web"])

            # Duration distribution
            if is_weekend:
                dur_min = rng.choices(
                    [15, 25, 40, 60, 90, 150, 240],
                    weights=[0.18, 0.18, 0.20, 0.20, 0.12, 0.08, 0.04],
                )[0]
            else:
                dur_min = rng.choices(
                    [10, 15, 25, 40, 60, 90, 120],
                    weights=[0.20, 0.25, 0.25, 0.15, 0.10, 0.04, 0.01],
                )[0]
            duration = dur_min * 60

            # Time of day — bias toward evening + occasional late-night
            hour_buckets = [(7, 9), (12, 16), (17, 21), (22, 25)]
            hour_weights = [0.10, 0.18, 0.50, 0.22]
            bucket = rng.choices(hour_buckets, weights=hour_weights, k=1)[0]
            hour = rng.randint(bucket[0], bucket[1])
            minute = rng.randint(0, 59)
            session_day = d
            if hour >= 24:
                hour = hour - 24
                session_day = d + timedelta(days=1)
            started_at = datetime.combine(session_day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)

            sessions.append(ReadingSession(
                user_id=user_id,
                book_id=book.id,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=duration),
                duration_seconds=duration,
                progress_start=0.0,
                progress_end=0.0,
                pages_turned=max(1, duration // 90),
                device=rng.choice(devices),
                session_uuid=str(uuid4()),
            ))

    # Binge weekend — pick a Saturday 35-60 days ago, dump 5 sessions on Berserk vol 1
    binge_day = today - timedelta(days=rng.randint(35, 60))
    while binge_day.weekday() != 5:
        binge_day -= timedelta(days=1)
    book = books_by_slug["berserk-01"]
    for slot in range(5):
        started = datetime.combine(binge_day, datetime.min.time()) + timedelta(hours=11 + slot * 2, minutes=rng.randint(0, 30))
        dur = rng.choice([55, 70, 85, 100]) * 60
        sessions.append(ReadingSession(
            user_id=user_id, book_id=book.id,
            started_at=started, ended_at=started + timedelta(seconds=dur),
            duration_seconds=dur, progress_start=0.0, progress_end=0.0,
            pages_turned=max(1, dur // 90),
            device="Kobo Libra Colour", session_uuid=str(uuid4()),
        ))

    real_sessions = sessions

    # ── Per-book statuses + positions ───────────────────────────────────────
    statuses: dict[str, UserBookStatus] = {}
    positions: dict[str, TomeSyncPosition] = {}

    # UserBookStatus.progress_pct is stored as a 0-1 fraction (BookDetailPage
    # multiplies by 100 for display). TomeSyncPosition.percentage is also 0-1.
    progress_overrides = {
        "good-guys-03":      0.674,   # Dungeon Mauling 67.4%
        "project-hail-mary": 0.42,    # Project Hail Mary 42%
    }
    finished_set = {s for s, _, _, st in lifecycle if st == "read"}
    reading_set  = set(currently_reading_slugs)
    # Stalled books deliberately get NO status record so they don't pollute "Currently Reading".

    for slug in reading_set:
        book = books_by_slug[slug]
        pct = progress_overrides.get(slug, 0.50)
        statuses[slug] = UserBookStatus(
            user_id=user_id, book_id=book.id, status="reading",
            progress_pct=pct,
            cfi=None, updated_at=datetime.utcnow() - timedelta(hours=rng.randint(1, 24)),
        )
        positions[slug] = TomeSyncPosition(
            user_id=user_id, book_id=book.id,
            progress=f"epubcfi(/6/{book.id*4}!/4/2[ch3]/2/4)",
            percentage=pct,
            device="Kindle Paperwhite",
            updated_at=datetime.utcnow() - timedelta(hours=rng.randint(1, 24)),
        )

    for slug in finished_set:
        book = books_by_slug[slug]
        ended_ago = next((e for s, _, e, st in lifecycle if s == slug), 100)
        statuses[slug] = UserBookStatus(
            user_id=user_id, book_id=book.id, status="read",
            progress_pct=1.0, cfi=None,
            updated_at=datetime.utcnow() - timedelta(days=ended_ago or 0),
        )

    return real_sessions, statuses, positions


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Resetting showcase dirs at {SHOWCASE_DIR}")
    reset_dirs()

    print("Creating fresh DB + schema")
    engine = init_db()
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # 1. Book types + auto-libraries
        print("Seeding book types + libraries")
        types = seed_book_types(db)
        db.commit()

        # 2. User
        print(f"Creating user '{USER['username']}'")
        user = User(
            username=USER["username"],
            email=USER["email"],
            hashed_password=hash_password(USER["password"]),
            is_active=True,
            is_admin=USER["is_admin"],
            role="admin",
            must_change_password=False,
        )
        db.add(user)
        db.flush()

        # 3. Books
        print(f"Inserting {len(BOOKS)} books")
        books_by_slug: dict[str, Book] = {}
        for slug, title, author, series, idx, year, fmt, type_slug, custom_desc in BOOKS:
            book_type = types[type_slug]
            description = custom_desc or SERIES_DESCRIPTIONS.get(series or "", None)
            file_dir = SHOWCASE_LIBRARY / (series or author or "Misc")
            file_dir.mkdir(parents=True, exist_ok=True)
            file_path = file_dir / f"{slug}.{fmt}"

            # If a real ebook exists at docs/seed/library/<slug>.<fmt>, copy it
            # and compute the real content hash. Otherwise, fake the hash and
            # don't write any actual file (BookFile gets a placeholder path).
            real_source = SEED_LIBRARY / f"{slug}.{fmt}"
            if real_source.exists():
                shutil.copy2(real_source, file_path)
                h = hashlib.sha256()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                content_hash = h.hexdigest()
                file_size = file_path.stat().st_size
            else:
                content_hash = fake_hash(f"{slug}|{title}")
                file_size = random.Random(slug).randint(2_000_000, 80_000_000)

            cover_filename = copy_cover(slug, content_hash)
            book = Book(
                title=title,
                author=author,
                series=series,
                series_index=float(idx) if idx else None,
                year=year,
                language="en",
                description=description,
                cover_path=cover_filename,
                content_hash=content_hash,
                book_type_id=book_type.id,
                status="active",
                is_reviewed=True,
                added_by=user.id,
                added_at=datetime.utcnow() - timedelta(days=random.Random(slug).randint(60, 400)),
            )
            db.add(book)
            db.flush()
            db.add(BookFile(
                book_id=book.id,
                file_path=str(file_path),
                format=fmt,
                file_size=file_size,
                content_hash=content_hash,
            ))
            type_lib = db.get(Library, book_type.library_id)
            if type_lib and book not in type_lib.books:
                type_lib.books.append(book)
            books_by_slug[slug] = book
        db.commit()

        # 3a. Series extensions — placeholder books for vols 4+ so series totals
        # match reality on the series-detail page. No covers, no files.
        ext_count = sum(len(v) for v in SERIES_EXTENSIONS.values())
        print(f"Inserting {ext_count} placeholder books for series extensions")
        for series_name, vols in SERIES_EXTENSIONS.items():
            # All placeholders go under the "novel" book type (Eric Ugland series)
            book_type = types["novel"]
            for vol_idx, vol_title in vols:
                ch = fake_hash(f"placeholder|{series_name}|{vol_idx}|{vol_title}")
                book = Book(
                    title=vol_title, author="Eric Ugland",
                    series=series_name, series_index=float(vol_idx),
                    year=None, language="en",
                    description=SERIES_DESCRIPTIONS.get(series_name),
                    cover_path=None, content_hash=ch,
                    book_type_id=book_type.id,
                    status="active", is_reviewed=True, added_by=user.id,
                    added_at=datetime.utcnow() - timedelta(days=random.Random(ch).randint(10, 300)),
                )
                db.add(book)
                db.flush()
                type_lib = db.get(Library, book_type.library_id)
                if type_lib and book not in type_lib.books:
                    type_lib.books.append(book)
        db.commit()

        # 4. Series metadata + arcs
        print(f"Inserting {len(SERIES_STATUS)} series-meta rows + {len(ARCS)} arcs")
        for series_name, status in SERIES_STATUS.items():
            db.add(SeriesMeta(series_name=series_name, status=status))
        for series_name, name, start, end, desc in ARCS:
            db.add(Arc(series_name=series_name, name=name, start_index=start, end_index=end, description=desc))
        db.commit()

        # 5. Reading sessions, statuses, positions
        print("Generating reading sessions + statuses + positions")
        sessions, statuses, positions = generate_sessions(db, books_by_slug, user.id)
        for s in sessions:
            db.add(s)
        for s in statuses.values():
            db.add(s)
        for p in positions.values():
            db.add(p)
        db.commit()

        # 6. A couple of shelves
        print("Adding 2 shelves")
        db.add(SavedFilter(
            name="Currently reading", icon="BookMarked", owner_id=user.id,
            params='{"reading_status":"reading"}', sort_order=1,
        ))
        db.add(SavedFilter(
            name="Manga", icon="Layers", owner_id=user.id,
            params='{"book_type":"manga"}', sort_order=2,
        ))
        db.commit()

        # Summary
        print()
        print(f"  Books:    {db.query(Book).count()}")
        print(f"  Sessions: {db.query(ReadingSession).count()}")
        print(f"  Status:   {db.query(UserBookStatus).count()}  (reading={db.query(UserBookStatus).filter_by(status='reading').count()}, read={db.query(UserBookStatus).filter_by(status='read').count()})")
        print(f"  Series:   {db.query(SeriesMeta).count()}  Arcs: {db.query(Arc).count()}")
        print(f"  Shelves:  {db.query(SavedFilter).count()}")
        print()
        print(f"Done. DB at {SHOWCASE_DIR/'tome.db'}")
        print(f"User: {USER['username']} / {USER['password']}  (admin)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
