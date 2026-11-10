"""The book page's progress line from a KOSync reader's position pushes.

The line (``own.session_timeline[].progress_pct`` of
``GET /api/books/{id}/reading-stats``) is the furthest position per reading
day, carried forward. Readers that speak only KOSync (CrossInk, KOReader's
stock plugin, Readest) upload sessions without ``progress_end``, so the
position comes from ``reading_history``: every ``PUT /syncs/progress`` is
logged there. Per day the furthest push merges with the sessions' own
progress by max; a push on a day without reading still lifts the days after.

Pushes are made through the real endpoint; only their ``synced_at`` is moved
to the day a test needs (the endpoint stamps "now").
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.book import BOOK_NO_FILE, Book
from backend.models.kosync import KOSyncDocumentMap, KOSyncUser, ReadingHistory
from backend.models.tome_sync import ReadingSession

STATS = "/api/v1/api/v1"
DOC = "0123456789abcdef0123456789abcdef"
DEVICE = "A1B2C3D4E5F6"
META = {"filename": "The Glass Orchard.epub", "title": "The Glass Orchard", "authors": "Mirela Vantongeren"}

# Noon UTC, ten days back: far enough from "now" that no test day is today.
D0 = (datetime.utcnow() - timedelta(days=10)).replace(hour=12, minute=0, second=0, microsecond=0)


def _day(n: int) -> str:
    return (D0 + timedelta(days=n)).date().isoformat()


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def user(admin_user):
    return admin_user[0]


@pytest.fixture()
def acc(db, user):
    """The admin's sync account under the Tome username (link-kosync finds it by name)."""
    row = KOSyncUser(username=user.username, userkey="k" * 32, user_id=user.id)
    db.add(row)
    db.flush()
    return row


def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _link(db, user, book, document=DOC):
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=document, book_id=book.id))
    db.flush()


def _push(client, db, acc, percentage, at: datetime, document=DOC, metadata=None):
    """A KOSync position push, logged as if it was made at ``at`` (UTC)."""
    body = {"document": document, "percentage": percentage, "progress": "/body/DocFragment[3]",
            "device": "CrossInk", "device_id": DEVICE}
    if metadata is not None:
        body["metadata"] = metadata
    r = client.put("/api/v1/syncs/progress", headers=_headers(acc), json=body)
    assert r.status_code == 200, r.text
    row = db.query(ReadingHistory).order_by(ReadingHistory.id.desc()).first()
    row.synced_at = at
    db.flush()


_sid = iter(range(1, 10_000))


def _session(client, acc, at: datetime, seconds=1800, document=DOC):
    """A CrossInk reading session: time, no pages, no progress."""
    start = int(at.replace(tzinfo=timezone.utc).timestamp())
    r = client.put(f"{STATS}/stats/sessions", headers=_headers(acc), json={
        "device_id": DEVICE, "items": [{
            "session_id": f"{next(_sid):032x}", "document": document, "started_at": start,
            "ended_at": start + seconds, "seconds": seconds, "utc_offset_minutes": 0,
        }],
    })
    assert r.status_code == 200, r.text


def _line(client, book, **params):
    r = client.get(f"/api/books/{book.id}/reading-stats", params=params)
    assert r.status_code == 200, r.text
    return {row["date"]: row["progress_pct"] for row in r.json()["own"]["session_timeline"]}


# ── the line from pushes ──────────────────────────────────────────────────────

def test_crossink_sessions_get_their_line_from_the_pushes(db, client, acc, user, make_book):
    book = make_book(title="The Glass Orchard")
    _link(db, user, book)
    for n, pct in enumerate((0.12, 0.31, 0.58)):
        _session(client, acc, D0 + timedelta(days=n))
        _push(client, db, acc, pct, D0 + timedelta(days=n, minutes=40))
    assert _line(client, book) == {_day(0): 12.0, _day(1): 31.0, _day(2): 58.0}


def test_without_pushes_the_line_stays_empty(db, client, acc, user, make_book):
    book = make_book(title="Quiet Lanterns")
    _link(db, user, book)
    _session(client, acc, D0)
    _session(client, acc, D0 + timedelta(days=1))
    assert _line(client, book) == {_day(0): None, _day(1): None}


def test_a_push_on_a_day_without_reading_lifts_the_days_after(db, client, acc, user, make_book):
    """Read in the evening, the reader syncs the next day without reading."""
    book = make_book(title="Salt Meridian")
    _link(db, user, book)
    _session(client, acc, D0)
    _push(client, db, acc, 0.10, D0 + timedelta(minutes=40))
    _push(client, db, acc, 0.40, D0 + timedelta(days=2))     # no session that day
    _session(client, acc, D0 + timedelta(days=4))
    _session(client, acc, D0 + timedelta(days=5))
    assert _line(client, book) == {_day(0): 10.0, _day(4): 40.0, _day(5): 40.0}


def test_a_downward_push_does_not_dip_the_line(db, client, acc, user, make_book):
    book = make_book(title="Copper Tidewater")
    _link(db, user, book)
    _session(client, acc, D0)
    _push(client, db, acc, 0.50, D0 + timedelta(minutes=10))
    _push(client, db, acc, 0.20, D0 + timedelta(minutes=50))   # opened an earlier chapter
    _session(client, acc, D0 + timedelta(days=1))
    _push(client, db, acc, 0.30, D0 + timedelta(days=1, minutes=30))
    _session(client, acc, D0 + timedelta(days=2))
    _push(client, db, acc, 0.55, D0 + timedelta(days=2, minutes=30))
    assert _line(client, book) == {_day(0): 50.0, _day(1): 50.0, _day(2): 55.0}


# ── pushes made before the document had a book ───────────────────────────────

def test_pushes_before_the_link_count_once_linked(db, client, acc, user, make_book):
    book = make_book(title="Harbour of Small Hours")
    # No metadata, no hash, no pending download: Tome cannot tell the book yet.
    _session(client, acc, D0)
    _push(client, db, acc, 0.15, D0 + timedelta(minutes=40))
    _session(client, acc, D0 + timedelta(days=1))
    _push(client, db, acc, 0.35, D0 + timedelta(days=1, minutes=40))
    assert db.query(ReadingHistory).filter(ReadingHistory.book_id.isnot(None)).count() == 0
    assert _line(client, book) == {}

    r = client.post(f"/api/books/{book.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    assert _line(client, book) == {_day(0): 15.0, _day(1): 35.0}


def test_pushes_before_the_link_count_once_a_later_push_links_it(db, client, acc, user, make_book):
    """The next push after the file became known (here: Tome now has its hash)."""
    from backend.models.ko_stats import KoHash

    book = make_book(title="Lamplighter's Atlas")
    _session(client, acc, D0)
    _push(client, db, acc, 0.22, D0 + timedelta(minutes=40))
    assert _line(client, book) == {}
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="baked"))
    db.flush()
    # This push links the document; the earlier push and session follow it.
    _push(client, db, acc, 0.47, D0 + timedelta(days=1, minutes=40))
    _session(client, acc, D0 + timedelta(days=1))
    assert _line(client, book) == {_day(0): 22.0, _day(1): 47.0}


def test_pushes_follow_a_hand_over_from_a_file_less_book(db, client, acc, user, make_book):
    # The reader named the file: the first push makes a file-less book.
    _session(client, acc, D0)
    _push(client, db, acc, 0.18, D0 + timedelta(minutes=40), metadata=META)
    stub = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert {r.book_id for r in db.query(ReadingHistory)} == {stub.id}

    real = make_book(title="The Glass Orchard (library copy)")
    r = client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    assert {r.book_id for r in db.query(ReadingHistory)} == {real.id}
    _session(client, acc, D0 + timedelta(days=1))
    assert _line(client, real) == {_day(0): 18.0, _day(1): 18.0}


def test_a_relink_moves_the_pushes(db, client, acc, user, make_book):
    first, second = make_book(title="Wrong Guess"), make_book(title="Right Book")
    _link(db, user, first)
    _session(client, acc, D0)
    _push(client, db, acc, 0.40, D0 + timedelta(minutes=40))
    assert _line(client, first) == {_day(0): 40.0}

    assert client.post(f"/api/books/{second.id}/link-kosync", json={"document": DOC}).status_code == 200
    assert _line(client, first) == {}
    assert _line(client, second) == {_day(0): 40.0}


# ── merging with session progress ────────────────────────────────────────────

def test_session_progress_higher_than_the_push_wins(db, client, acc, user, make_book):
    book = make_book(title="Fennel and Iron")
    _link(db, user, book)
    _session(client, acc, D0)
    _push(client, db, acc, 0.30, D0 + timedelta(minutes=40))
    # A web-reader sitting the same day that got further.
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=D0 + timedelta(hours=3),
                          ended_at=D0 + timedelta(hours=3, minutes=20), duration_seconds=1200,
                          pages_turned=10, device="web", progress_end=0.45))
    db.flush()
    _session(client, acc, D0 + timedelta(days=1))
    _push(client, db, acc, 0.40, D0 + timedelta(days=1, minutes=40))
    assert _line(client, book) == {_day(0): 45.0, _day(1): 45.0}


def test_a_tomesync_only_book_is_unchanged(db, client, user, make_book):
    book = make_book(title="Undertow Gazette")
    for n, p in ((0, 0.2), (2, 0.6)):
        db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=D0 + timedelta(days=n),
                              ended_at=D0 + timedelta(days=n, minutes=20), duration_seconds=1200,
                              pages_turned=12, device="Kindle", progress_end=p))
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=D0 + timedelta(days=1),
                          ended_at=D0 + timedelta(days=1, minutes=20), duration_seconds=1200,
                          pages_turned=12, device="Kindle", progress_end=None))
    db.flush()
    assert _line(client, book) == {_day(0): 20.0, _day(1): 20.0, _day(2): 60.0}


def test_another_users_pushes_never_count(db, client, acc, user, make_book):
    from backend.core.security import hash_password
    from backend.models.user import User

    book = make_book(title="Shared Shelf")
    other = User(username="reader2", email="reader2@example.com", hashed_password=hash_password("pass"),
                 is_active=True, role="member", must_change_password=False)
    db.add(other)
    db.flush()
    other_acc = KOSyncUser(username="reader2", userkey="k" * 32, user_id=other.id)
    db.add(other_acc)
    db.flush()
    _link(db, user, book)
    _link(db, other, book)
    _session(client, acc, D0)
    _push(client, db, other_acc, 0.9, D0 + timedelta(minutes=40))
    assert _line(client, book) == {_day(0): None}


# ── odd values ────────────────────────────────────────────────────────────────

def test_values_out_of_range_are_clamped(db, client, acc, user, make_book):
    book = make_book(title="Brass Weather")
    _link(db, user, book)
    _session(client, acc, D0)
    _push(client, db, acc, -0.3, D0 + timedelta(minutes=40))
    _session(client, acc, D0 + timedelta(days=1))
    _push(client, db, acc, 1.005, D0 + timedelta(days=1, minutes=40))   # float noise past the end
    assert _line(client, book) == {_day(0): None, _day(1): 100.0}


def test_a_value_above_one_reads_as_the_end(db, client, acc, user, make_book):
    """The percentage is a fraction and no client sends percent, so nothing
    is rescaled: anything above 1 is the end of the book."""
    book = make_book(title="Moth Cartography")
    _link(db, user, book)
    _session(client, acc, D0)
    _push(client, db, acc, 0.2, D0 + timedelta(minutes=40))
    _session(client, acc, D0 + timedelta(days=1))
    _push(client, db, acc, 42, D0 + timedelta(days=1, minutes=40))
    assert _line(client, book) == {_day(0): 20.0, _day(1): 100.0}


def test_pushes_without_any_session_draw_no_line(db, client, acc, user, make_book):
    """The line lives on reading days. A KOSync app that uploads no sessions
    (KOReader's stock plugin, Readest) has none, so pushes alone draw nothing."""
    book = make_book(title="Lantern Ledger")
    _link(db, user, book)
    _push(client, db, acc, 0.2, D0)
    _push(client, db, acc, 0.5, D0 + timedelta(days=1))
    assert _line(client, book) == {}


# ── day bucketing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("params", [{"tz_offset": -300}, {"tz": "Asia/Karachi"}])
def test_pushes_use_the_same_reading_day_as_sessions(db, client, acc, user, make_book, params):
    """A push at 23:30 UTC is the same reading day in UTC (rollover 04:00) but
    the next day at UTC+5 (04:30 local), where a session sits too."""
    book = make_book(title="Northbound Clockwork")
    _link(db, user, book)
    _session(client, acc, D0)                                  # 17:00 at UTC+5, day 0 both ways
    _push(client, db, acc, 0.30, D0 + timedelta(minutes=30))
    _session(client, acc, D0 + timedelta(hours=11, minutes=45))   # 23:45 UTC; 04:45 day 1 at UTC+5
    _push(client, db, acc, 0.60, D0 + timedelta(hours=11, minutes=30))

    assert _line(client, book) == {_day(0): 60.0}
    assert _line(client, book, **params) == {_day(0): 30.0, _day(1): 60.0}


# ── the index behind it ──────────────────────────────────────────────────────

def _indexes(conn, table):
    from sqlalchemy import text
    return {
        row[1]: [c[2] for c in conn.execute(text(f"PRAGMA index_info({row[1]})"))]
        for row in conn.execute(text(f"PRAGMA index_list({table})"))
    }


def test_the_startup_migration_adds_the_user_book_index():
    """reading_history as an older database has it (single-column indexes
    only): the migration adds (user_id, book_id), runs twice, and the
    linking UPDATE then seeks it instead of walking the user's rows."""
    from sqlalchemy import create_engine, text

    from backend.main import migrate_reading_history_index

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        migrate_reading_history_index(conn)          # no table yet: nothing to do
        conn.execute(text(
            "CREATE TABLE reading_history (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "book_id INTEGER, document VARCHAR(64) NOT NULL, percentage FLOAT NOT NULL, "
            "device VARCHAR(255), synced_at DATETIME NOT NULL)"))
        conn.execute(text("CREATE INDEX ix_reading_history_user_id ON reading_history (user_id)"))
        conn.execute(text("CREATE INDEX ix_reading_history_book_id ON reading_history (book_id)"))
        conn.commit()
        assert "ix_reading_history_user_book" not in _indexes(conn, "reading_history")

        migrate_reading_history_index(conn)
        migrate_reading_history_index(conn)          # idempotent
        assert _indexes(conn, "reading_history")["ix_reading_history_user_book"] == ["user_id", "book_id"]
        plan = " ".join(str(r[3]) for r in conn.execute(text(
            "EXPLAIN QUERY PLAN UPDATE reading_history SET book_id = 5 "
            "WHERE user_id = 1 AND lower(document) = 'x' AND book_id IS NULL")))
        assert "ix_reading_history_user_book" in plan


def test_a_new_database_has_the_index():
    from sqlalchemy import create_engine

    from backend.core.database import Base
    import backend.models.kosync  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["reading_history"]])
    with engine.connect() as conn:
        assert _indexes(conn, "reading_history")["ix_reading_history_user_book"] == ["user_id", "book_id"]
