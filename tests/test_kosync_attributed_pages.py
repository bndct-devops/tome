"""Pages per session from a CrossInk reader's page counter.

The reader uploads sessions with time and no pages, and per book a lifetime
counter of forward page turns that only grows (``PUT .../stats/books``). The
growth between two snapshots of one (user, device, document) is the pages
turned between them; Tome splits it over that device's sessions of that
document that end in between, by reading seconds, and marks them
``pages_attributed``. The book's all-time pages stay equal to the counter.

Snapshot times are the server's clock, so a test moves the stored snapshot
back in time (``_age``) to stand for an earlier sync, then uploads sessions
that end after it.
"""
import time
from datetime import datetime, timedelta

import pytest

from backend.models.ko_stats import PageStat
from backend.models.kosync import KOSyncBookStats, KOSyncSessionReceipt
from backend.models.tome_sync import ReadingSession
from backend.services import kosync_stats
from backend.services.reading_day import DayCtx
from tests.test_kosync_book_pages import (
    BASE,
    DEVICE,
    DEVICE2,
    DOC,
    DOC2,
    _account,
    _delivered,
    _headers,
    _item,
    _jwt,
    _link,
    _member,
    _own,
    _per_book,
    _put_books,
)

HOUR = 3600


# ── helpers ───────────────────────────────────────────────────────────────────

def _snap(client, acc, pages, seconds=None, document=DOC, device_id=DEVICE):
    seconds = pages * 30 if seconds is None else seconds
    r = _put_books(client, acc, _item(document=document, pages=pages, seconds=seconds), device_id=device_id)
    assert _delivered(r, book_count=1), r.text
    assert set(r.json()) == {"until", "accepted", "accepted_daily"} and r.json()["accepted_daily"] == 0
    return r


def _row(db, document=DOC, device_id=DEVICE):
    db.expire_all()
    return db.query(KOSyncBookStats).filter_by(document=document, device_id=device_id).one()


def _age(db, hours, document=DOC, device_id=DEVICE):
    """The stored snapshot was sent ``hours`` ago (an earlier sync): it and
    everything Tome received up to it move back in time together, keeping
    their order (sessions are placed in windows by when Tome received them)."""
    row = _row(db, document, device_id)
    old = row.updated_at
    row.updated_at = datetime.utcnow() - timedelta(hours=hours)
    shift = old - row.updated_at
    for receipt in db.query(KOSyncSessionReceipt).filter(KOSyncSessionReceipt.received_at <= old):
        receipt.received_at -= shift
    if row.pending_since is not None and row.pending_since > row.updated_at:
        row.pending_since = row.updated_at - timedelta(minutes=1)
    db.flush()


def _sess(client, acc, sid, ends_ago_h, seconds, document=DOC, device_id=DEVICE, span=None, start=None):
    """A reader session ending ``ends_ago_h`` hours ago (or, with ``start``,
    that session sent again ``span`` seconds long)."""
    if start is None:
        end = int(time.time() - ends_ago_h * HOUR)
        start = end - (seconds if span is None else span)
    else:
        end = start + (seconds if span is None else span)
    r = client.put(f"{BASE}/stats/sessions", headers=_headers(acc), json={
        "device_id": device_id, "items": [{
            "session_id": sid, "document": document, "started_at": start, "ended_at": end,
            "seconds": seconds, "utc_offset_minutes": 0,
        }],
    })
    assert r.status_code == 200 and r.json()["accepted_session_ids"] == [sid], r.text
    return start


def _reading(db, sid):
    db.expire_all()
    receipt = db.query(KOSyncSessionReceipt).filter_by(session_uuid=sid).one()
    return receipt.reading_session


def _pages(db, *sids):
    return [(_reading(db, s).pages_turned, _reading(db, s).pages_attributed) for s in sids]


def _sid(n):
    return f"{n:032x}"


@pytest.fixture()
def user(admin_user):
    return admin_user[0]


@pytest.fixture()
def acc(db, user):
    return _account(db, user)


@pytest.fixture()
def book(db, user, make_book):
    b = make_book(title="Counted On The Reader")
    _link(db, user, b)
    return b


# ── the split ────────────────────────────────────────────────────────────────

def test_split_adds_up_exactly_by_largest_remainder():
    assert kosync_stats.split(40, [1800, 600]) == [30, 10]
    assert kosync_stats.split(10, [1000, 1000, 1000]) == [4, 3, 3]
    assert kosync_stats.split(7, [100, 200, 300]) == [1, 2, 4]   # 1.17, 2.33, 3.5
    assert kosync_stats.split(1, [10, 3000]) == [0, 1]
    assert kosync_stats.split(0, [10, 20]) == [0, 0]
    for amount in range(0, 60):
        assert sum(kosync_stats.split(amount, [13, 700, 41, 999])) == amount


# ── two snapshots, sessions in between ──────────────────────────────────────

def test_one_session_between_two_snapshots_gets_the_growth(db, client, acc, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(1), 5, 1800)
    r = _snap(client, acc, 137)
    assert _pages(db, _sid(1)) == [(37, True)]
    row = _row(db)
    assert (row.pages_attributed, row.pages_pending, row.pending_since) == (37, 0, None)
    # All-time pages are still exactly the counter.
    assert _own(client, book)["pages_turned"] == 137
    assert kosync_stats.reader_pages(db, book.added_by)[book.id].pages == 100
    assert _delivered(r, book_count=1)


def test_several_sessions_split_by_seconds_and_sum_exactly(db, client, acc, book):
    _snap(client, acc, 50)
    _age(db, 12)
    _sess(client, acc, _sid(1), 9, 1000)
    _sess(client, acc, _sid(2), 6, 1000)
    _sess(client, acc, _sid(3), 3, 1000)
    _snap(client, acc, 60)
    assert _pages(db, _sid(1), _sid(2), _sid(3)) == [(4, True), (3, True), (3, True)]
    assert _own(client, book)["pages_turned"] == 60


def test_only_sessions_inside_the_window_and_never_twice(db, client, acc, book):
    _sess(client, acc, _sid(1), 20, 1200)     # before the first snapshot: never attributed
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(2), 8, 1800)
    _sess(client, acc, _sid(3), 4, 600)
    _snap(client, acc, 140)
    assert _pages(db, _sid(1), _sid(2), _sid(3)) == [(None, False), (30, True), (10, True)]
    _age(db, 2)
    _sess(client, acc, _sid(4), 1, 900)
    _snap(client, acc, 152)
    assert _pages(db, _sid(2), _sid(3), _sid(4)) == [(30, True), (10, True), (12, True)]
    assert _own(client, book)["pages_turned"] == 152
    assert _row(db).pages_attributed == 52


def test_a_snapshot_with_no_growth_closes_its_window(db, client, acc, book):
    """Zero growth is an answer: the session turned no pages, and it can never
    take a later window's growth."""
    _snap(client, acc, 100, seconds=3000)
    _age(db, 5)
    _sess(client, acc, _sid(1), 2, 600)
    _snap(client, acc, 100, seconds=3600)    # seconds changed, pages did not
    assert _pages(db, _sid(1)) == [(0, True)]
    assert (_row(db).pages_pending, _row(db).pages_attributed) == (0, 0)
    _age(db, 1)
    _sess(client, acc, _sid(2), 0.5, 600)
    _snap(client, acc, 120)
    assert _pages(db, _sid(1), _sid(2)) == [(0, True), (20, True)]
    assert _own(client, book)["pages_turned"] == 120


# ── snapshot before its sessions ─────────────────────────────────────────────

def test_pending_growth_waits_for_the_next_snapshot_and_takes_every_late_session(db, client, acc, book):
    """The session upload failed partway, the snapshot still went. The late
    sessions arrive one per request on the next sync; the old window is given
    out only at the next snapshot, over all of them, and the new growth over
    the new window."""
    _snap(client, acc, 100)
    _age(db, 10)
    _snap(client, acc, 130)                    # pending 30 over (10 h ago, now]
    row = _row(db)
    assert row.pages_pending == 30 and row.pending_since is not None
    assert _own(client, book)["pages_turned"] == 130
    _age(db, 3)                                # that snapshot was 3 hours ago

    _sess(client, acc, _sid(1), 7, 1200)       # late sessions of the old window
    assert _pages(db, _sid(1)) == [(None, False)]
    _sess(client, acc, _sid(2), 5, 600)
    _sess(client, acc, _sid(3), 1, 900)        # read after that snapshot
    assert _pages(db, _sid(1), _sid(2), _sid(3)) == [(None, False)] * 3
    row = _row(db)
    assert (row.pages_pending, row.pages_attributed) == (30, 0)
    assert _own(client, book)["pages_turned"] == 130

    _snap(client, acc, 145)
    # Old window: 30 by 1200:600. New window: 15 to the one session in it.
    assert _pages(db, _sid(1), _sid(2), _sid(3)) == [(20, True), (10, True), (15, True)]
    row = _row(db)
    assert (row.pages_pending, row.pending_since, row.pages_attributed) == (0, None, 45)
    assert _own(client, book)["pages_turned"] == 145


def test_a_session_after_the_pending_snapshot_does_not_take_its_growth(db, client, acc, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _snap(client, acc, 130)
    _age(db, 3)                                # that snapshot was 3 hours ago
    _sess(client, acc, _sid(1), 6, 1200)       # a late session of that window
    _sess(client, acc, _sid(2), 1, 600)        # read after it: belongs to the next growth
    assert _row(db).pages_pending == 30
    _snap(client, acc, 131)
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (1, True)]
    assert _own(client, book)["pages_turned"] == 131


def test_pending_growth_goes_with_the_next_snapshot_at_the_latest(db, client, acc, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _snap(client, acc, 130)                    # pending 30, its sessions never come
    _age(db, 3)
    _sess(client, acc, _sid(1), 1, 600)
    _snap(client, acc, 140)
    assert _pages(db, _sid(1)) == [(40, True)]
    assert _row(db).pages_pending == 0
    assert _own(client, book)["pages_turned"] == 140


def test_late_sessions_of_the_pending_window_take_it_at_the_next_snapshot(db, client, acc, book):
    """The snapshot goes first, the sessions of that window and of the next
    one arrive later together: each window's growth stays in its window."""
    _snap(client, acc, 100)
    _age(db, 10)
    _snap(client, acc, 130)                    # pending 30 over (10 h ago, 3 h ago]
    _age(db, 3)
    row = _row(db)
    row.pages_pending, row.pending_since = 30, datetime.utcnow() - timedelta(hours=10)
    db.flush()
    # The next sync: both sessions in one request, then the snapshot.
    end_old, end_new = int(time.time() - 5 * HOUR), int(time.time() - HOUR)
    r = client.put(f"{BASE}/stats/sessions", headers=_headers(acc), json={
        "device_id": DEVICE, "items": [
            {"session_id": _sid(1), "document": DOC, "started_at": end_old - 900,
             "ended_at": end_old, "seconds": 900, "utc_offset_minutes": 0},
            {"session_id": _sid(2), "document": DOC, "started_at": end_new - 900,
             "ended_at": end_new, "seconds": 900, "utc_offset_minutes": 0},
        ],
    })
    assert r.status_code == 200
    assert _pages(db, _sid(1), _sid(2)) == [(None, False), (None, False)]   # not before the snapshot
    _snap(client, acc, 150)
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (20, True)]
    assert _own(client, book)["pages_turned"] == 150


# ── reset ─────────────────────────────────────────────────────────────────────

def test_a_lower_counter_is_a_reset_and_never_a_negative_delta(db, client, acc, book, caplog):
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(1), 5, 1200)
    _snap(client, acc, 140)
    assert _pages(db, _sid(1)) == [(40, True)]
    _age(db, 4)
    _sess(client, acc, _sid(2), 2, 600)
    with caplog.at_level("INFO", logger="backend.services.kosync_stats"):
        _snap(client, acc, 10)                 # "Delete book stats" on the reader, then 10 pages
    assert "treating it as a reset" in caplog.text
    row = _row(db)
    assert (row.counter_epoch, row.pages_attributed, row.pages_pending) == (1, 0, 0)
    # Nothing attributed from the reset (its window is closed with 0); the
    # earlier session keeps its pages.
    assert _pages(db, _sid(1), _sid(2)) == [(40, True), (0, True)]
    # Tome keeps the pages its sessions hold; the reader's counter restarts.
    assert _own(client, book)["pages_turned"] == 40 + 10

    _age(db, 1)
    _sess(client, acc, _sid(3), 0.5, 600)
    _snap(client, acc, 25)
    assert _pages(db, _sid(3)) == [(15, True)]
    assert kosync_stats.reader_pages(db, book.added_by)[book.id].pages == 10
    assert _own(client, book)["pages_turned"] == 40 + 25


# ── devices, documents, Tome's own pages ─────────────────────────────────────

def test_two_devices_attribute_separately(db, client, acc, book):
    _snap(client, acc, 100)
    _snap(client, acc, 20, device_id=DEVICE2)
    _age(db, 10)
    _age(db, 10, device_id=DEVICE2)
    _sess(client, acc, _sid(1), 5, 1200)
    _sess(client, acc, _sid(2), 5, 1200, device_id=DEVICE2)
    _snap(client, acc, 130)
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (None, False)]
    assert _own(client, book)["pages_turned"] == 130 + 20
    _snap(client, acc, 27, device_id=DEVICE2)
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (7, True)]
    assert _own(client, book)["pages_turned"] == 130 + 27


def test_two_readers_on_one_document_interleaved_in_time(db, client, acc, book):
    """Two readers with the same file, each with its own counter: syncs and
    sessions alternate, and each device's growth goes only to its sessions."""
    _snap(client, acc, 100, device_id=DEVICE)
    _snap(client, acc, 50, device_id=DEVICE2)
    _age(db, 12, device_id=DEVICE)
    _age(db, 11, device_id=DEVICE2)
    _sess(client, acc, _sid(1), 10, 600, device_id=DEVICE)
    _sess(client, acc, _sid(2), 9, 1200, device_id=DEVICE2)
    _sess(client, acc, _sid(3), 8, 1200, device_id=DEVICE)
    _snap(client, acc, 130, device_id=DEVICE)            # A: 30 over A1, A3
    assert _pages(db, _sid(1), _sid(2), _sid(3)) == [(10, True), (None, False), (20, True)]
    _age(db, 7, device_id=DEVICE)
    _sess(client, acc, _sid(4), 6, 600, device_id=DEVICE2)
    _sess(client, acc, _sid(5), 5, 900, device_id=DEVICE)
    _snap(client, acc, 80, device_id=DEVICE2)            # B: 30 over B1, B2 (since 11 h ago)
    assert _pages(db, _sid(2), _sid(4), _sid(5)) == [(20, True), (10, True), (None, False)]
    _age(db, 4, device_id=DEVICE2)
    _sess(client, acc, _sid(6), 3, 900, device_id=DEVICE)
    _sess(client, acc, _sid(7), 2, 300, device_id=DEVICE2)
    _snap(client, acc, 150, device_id=DEVICE)            # A: 20 over A5, A6
    assert _pages(db, _sid(5), _sid(6), _sid(7)) == [(10, True), (10, True), (None, False)]
    assert _own(client, book)["pages_turned"] == 150 + 80
    _snap(client, acc, 83, device_id=DEVICE2)            # B: 3 to B3
    assert _pages(db, _sid(7)) == [(3, True)]
    assert (_row(db, device_id=DEVICE).pages_attributed, _row(db, device_id=DEVICE2).pages_attributed) == (50, 33)
    assert _own(client, book)["pages_turned"] == 150 + 83
    assert kosync_stats.reader_pages(db, book.added_by)[book.id].pages == 100 + 50


def test_two_documents_of_one_book_attribute_separately(db, client, acc, user, book):
    _link(db, user, book, DOC2)
    _snap(client, acc, 100, document=DOC)
    _snap(client, acc, 5, document=DOC2)
    _age(db, 10, document=DOC)
    _age(db, 10, document=DOC2)
    _sess(client, acc, _sid(1), 5, 1200, document=DOC)
    _sess(client, acc, _sid(2), 4, 1200, document=DOC2)
    _snap(client, acc, 120, document=DOC)
    _snap(client, acc, 9, document=DOC2)
    assert _pages(db, _sid(1), _sid(2)) == [(20, True), (4, True)]
    assert _own(client, book)["pages_turned"] == 120 + 9


def test_tome_counted_sessions_are_never_touched_and_add_on_top(db, client, acc, user, book):
    _snap(client, acc, 100, seconds=6000)
    _age(db, 10)
    start = datetime.utcnow() - timedelta(hours=6)
    kindle = ReadingSession(user_id=user.id, book_id=book.id, started_at=start,
                            ended_at=start + timedelta(hours=1), duration_seconds=3600,
                            pages_turned=60, device="Kindle")
    db.add(kindle)
    db.flush()
    _sess(client, acc, _sid(1), 5, 1800)
    _snap(client, acc, 130, seconds=7800)
    db.expire_all()
    assert (kindle.pages_turned, kindle.pages_attributed) == (60, False)
    assert _pages(db, _sid(1)) == [(30, True)]
    own = _own(client, book)
    assert own["pages_turned"] == 60 + 130
    # Pace: Kindle 60 p / 60 min, the attributed session 30 p / 30 min, the
    # counter's rest 100 p / (7800 - 1800) s = 100 min: 190 p / 190 min.
    assert own["pace_pages_per_min"] == pytest.approx(1.0)


def test_pace_keeps_the_readers_own_pace_after_attribution(db, client, acc, book):
    _snap(client, acc, 300, seconds=6000)      # 3 pages a minute
    before = _own(client, book)["pace_pages_per_min"]
    _age(db, 10)
    _sess(client, acc, _sid(1), 5, 1200)
    _snap(client, acc, 360, seconds=7200)      # 60 pages in 20 minutes
    own = _own(client, book)
    assert own["pages_turned"] == 360
    assert before == pytest.approx(3.0) and own["pace_pages_per_min"] == pytest.approx(3.0)


# ── trim, delete, re-send ────────────────────────────────────────────────────

def _attributed_pair(db, client, acc):
    _snap(client, acc, 100)
    _age(db, 10)
    starts = (_sess(client, acc, _sid(1), 6, 1800), _sess(client, acc, _sid(2), 3, 600))
    _snap(client, acc, 140)
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (10, True)]
    return starts


def test_a_trimmed_session_keeps_its_pages(db, client, acc, book):
    _attributed_pair(db, client, acc)
    sid = _reading(db, _sid(1)).id
    r = client.patch(f"/api/stats/sessions/{sid}", json={"duration_seconds": 600})
    assert r.status_code == 200, r.text
    s = _reading(db, _sid(1))
    assert (s.duration_seconds, s.pages_turned, s.pages_attributed) == (600, 30, True)
    assert _own(client, book)["pages_turned"] == 140
    # A later snapshot does not hand it pages again.
    _age(db, 1)
    _snap(client, acc, 150)
    assert _pages(db, _sid(1)) == [(30, True)]
    assert _own(client, book)["pages_turned"] == 150


def test_a_deleted_sessions_pages_fall_back_to_the_counter(db, client, acc, book):
    starts = _attributed_pair(db, client, acc)
    sid = _reading(db, _sid(1)).id
    assert client.delete(f"/api/stats/sessions/{sid}").status_code == 200
    db.expire_all()
    assert db.query(ReadingSession).filter_by(id=sid).first() is None
    assert kosync_stats.reader_pages(db, book.added_by)[book.id].pages == 130
    assert _own(client, book)["pages_turned"] == 140
    # The reader sends it again: it stays deleted and takes nothing.
    _sess(client, acc, _sid(1), 0, 1800, start=starts[0])
    db.expire_all()
    assert db.query(KOSyncSessionReceipt).filter_by(session_uuid=_sid(1)).one().reading_session_id is None
    _age(db, 1)
    _sess(client, acc, _sid(3), 0.5, 600)
    _snap(client, acc, 150)
    assert _pages(db, _sid(2), _sid(3)) == [(10, True), (10, True)]
    assert _own(client, book)["pages_turned"] == 150


def test_a_resent_session_never_duplicates_or_resets_its_pages(db, client, acc, book):
    starts = _attributed_pair(db, client, acc)
    _sess(client, acc, _sid(1), 0, 1800, start=starts[0])   # repeat
    _sess(client, acc, _sid(2), 0, 660, start=starts[1])    # a later checkpoint of the same sitting
    db.expire_all()
    assert db.query(ReadingSession).count() == 2
    assert _pages(db, _sid(1), _sid(2)) == [(30, True), (10, True)]
    assert _reading(db, _sid(2)).duration_seconds == 660
    assert _own(client, book)["pages_turned"] == 140


def test_a_sitting_across_two_syncs_takes_the_pages_of_both_windows(db, client, acc, book):
    """The sitting went on past the sync: the firmware re-sends it with a later
    end. It took pages for the time it had; the next window gives it a share
    for the time it gained since."""
    _snap(client, acc, 100)
    _age(db, 10)
    start = _sess(client, acc, _sid(1), 5, 1200)
    _snap(client, acc, 120)
    assert _pages(db, _sid(1)) == [(20, True)]
    _age(db, 4)
    _sess(client, acc, _sid(1), 0, 2400, start=start, span=int(4.5 * HOUR))   # 20 more minutes
    _sess(client, acc, _sid(2), 0.2, 600)                                     # and another sitting
    _snap(client, acc, 150)                                                   # 30 over 1200 s : 600 s
    assert _pages(db, _sid(1), _sid(2)) == [(40, True), (10, True)]
    row = _row(db)
    assert (row.pages_pending, row.pages_attributed) == (0, 50)
    assert _own(client, book)["pages_turned"] == 150
    assert _own(client, book)["pace_pages_per_min"] == pytest.approx(2.0)


# ── import ────────────────────────────────────────────────────────────────────

def test_an_imported_snapshot_is_a_base_for_live_growth(db, client, acc, user, book):
    """crosspoint_import stores snapshots through stage_books with the
    export's times; nothing is attributed from them."""
    sent = datetime.utcnow() - timedelta(hours=10)
    end = datetime.utcnow() - timedelta(hours=12)        # imported history, before the export
    kosync_stats.stage_sessions(db, user.id, DEVICE, [kosync_stats._SessionItem(
        _sid(1), DOC, end - timedelta(seconds=1200), end, 1200)], lambda d: book.id, imported=True)
    kosync_stats.stage_books(db, user.id, DEVICE, {DOC: _item(pages=100)}, sent_at={DOC: sent})
    db.flush()
    row = _row(db)
    assert (row.pages, row.updated_at, row.pages_attributed) == (100, sent, 0)
    assert _pages(db, _sid(1)) == [(None, False)]
    _sess(client, acc, _sid(2), 5, 1200)
    _snap(client, acc, 125)                              # the first live snapshot after the import
    assert _pages(db, _sid(1), _sid(2)) == [(None, False), (25, True)]
    assert _own(client, book)["pages_turned"] == 125


def test_an_import_newer_than_the_live_row_rebases_without_attributing(db, client, acc, user, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(1), 5, 1200)
    db.expire_all()   # it reached Tome when it ended, 5 hours ago, before the export below
    db.query(KOSyncSessionReceipt).filter_by(session_uuid=_sid(1)).one().received_at = (
        datetime.utcnow() - timedelta(hours=5))
    db.flush()
    kosync_stats.stage_books(db, user.id, DEVICE, {DOC: _item(pages=150)},
                             sent_at={DOC: datetime.utcnow() - timedelta(hours=2)})
    db.flush()
    assert _pages(db, _sid(1)) == [(None, False)]
    assert (_row(db).pages, _row(db).pages_pending) == (150, 0)
    assert _own(client, book)["pages_turned"] == 150
    _sess(client, acc, _sid(2), 1, 600)
    _snap(client, acc, 170)
    assert _pages(db, _sid(1), _sid(2)) == [(None, False), (20, True)]
    assert _own(client, book)["pages_turned"] == 170
    # A lower imported counter is a reset, as live.
    kosync_stats.stage_books(db, user.id, DEVICE, {DOC: _item(pages=5)},
                             sent_at={DOC: datetime.utcnow() + timedelta(minutes=1)})
    db.flush()
    assert _row(db).counter_epoch == 1
    assert _own(client, book)["pages_turned"] == 20 + 5


# ── what the user sees ───────────────────────────────────────────────────────

def test_every_dated_tile_now_shows_the_readers_pages(db, client, acc, user, book):
    r = client.post("/api/goals", json={"kind": "pages_per_week", "target": 100})
    assert r.status_code == 201, r.text
    _snap(client, acc, 100)
    _age(db, 1)
    start = _sess(client, acc, _sid(1), 0.25, 1800)
    before = {
        "daily": sum(d["pages"] for d in client.get("/api/stats?days=7").json()["daily"]),
        "home": client.get("/api/home/stats").json()["pages_turned_30d"],
    }
    assert before == {"daily": 0, "home": 0}
    _snap(client, acc, 142)

    day = DayCtx(0, None).py_day(start).isoformat()
    card = client.get(f"/api/stats/calendar/day?day={day}").json()
    assert card["pages"] == 42
    assert [(b["book_id"], b["pages"], b["pages_attributed"]) for b in card["books"]] == [(book.id, 42, True)]
    month = client.get(f"/api/stats/calendar?month={day[:7]}").json()
    assert month["totals"]["pages"] == 42

    stats = client.get("/api/stats?days=7").json()
    assert sum(d["pages"] for d in stats["daily"]) == 42
    assert [p["pages_turned"] for p in stats["reading_pace"]] == [42]
    assert stats["records"]["most_pages_day"] == 42
    assert _per_book(client, 0)[book.id] == 142          # all time: the counter
    assert _per_book(client, 7)[book.id] == 42           # a window: the session

    rows = client.get("/api/stats/sessions").json()["sessions"]
    assert [(s["pages_turned"], s["pages_attributed"]) for s in rows] == [(42, True)]
    rows = client.get(f"/api/stats/sessions?book_id={book.id}").json()["sessions"]
    assert [(s["pages_turned"], s["pages_attributed"]) for s in rows] == [(42, True)]

    assert client.get("/api/home/stats").json()["pages_turned_30d"] == 42
    activity = client.get("/api/home/activity").json()
    assert [(a["pages_turned"], a["pages_attributed"]) for a in activity] == [(42, True)]

    goal = client.get("/api/goals").json()["goals"][0]
    assert goal["current"] == 42

    timeline = _own(client, book)["session_timeline"]
    assert sum(d["pages"] for d in timeline) == 42


def test_the_backup_carries_the_new_columns(db, client, acc, book):
    _attributed_pair(db, client, acc)
    data = client.get("/api/auth/me/backup").json()
    sessions = data["reading_sessions"]
    assert sorted((s["pages_turned"], s["pages_attributed"]) for s in sessions) == [(10, True), (30, True)]
    stats = data["kosync_legacy"]["book_stats"]
    assert [(b["pages"], b["pages_attributed"], b["pages_pending"], b["counter_epoch"]) for b in stats] == [
        (140, 40, 0, 0)]


# ── runaway-session checks ───────────────────────────────────────────────────

def test_attributed_pages_do_not_make_reader_sessions_suspect(db, client, acc, book):
    """A slow window (2 pages over 50 minutes, 25 minutes a page) would read
    as idle time if the attributed pages counted; the checks see the session
    as they did before it had pages."""
    _snap(client, acc, 100)
    _age(db, 20)
    _sess(client, acc, _sid(1), 15, 3000)
    _snap(client, acc, 102)
    _age(db, 10)
    _sess(client, acc, _sid(2), 1, 7 * HOUR)            # long enough to be flagged on its own
    _snap(client, acc, 200)
    rows = {r["duration_seconds"]: r for r in client.get("/api/stats/sessions").json()["sessions"]}
    assert (rows[3000]["pages_turned"], rows[3000]["pages_attributed"]) == (2, True)
    assert rows[3000]["suspect"] is False and rows[3000]["suggested_seconds"] is None
    assert (rows[7 * HOUR]["pages_turned"], rows[7 * HOUR]["pages_attributed"]) == (98, True)
    # Flagged as before (its length), but no trim suggestion priced from pages
    # that were themselves spread by time.
    assert rows[7 * HOUR]["suspect"] is True and rows[7 * HOUR]["suggested_seconds"] is None


def test_attributed_sessions_stay_out_of_the_median_pace(db, client, acc, user, book):
    from backend.services.session_hygiene import median_secs_per_page
    _snap(client, acc, 100)
    _age(db, 10)
    for n in range(1, 5):
        _sess(client, acc, _sid(n), 9 - n, 600)
    _snap(client, acc, 140)                       # 10 pages per 10 minutes: 60 s a page
    assert median_secs_per_page(db, user.id) is None


def _lifetime(client, headers=None):
    return client.get("/api/stats?days=0", headers=headers or {}).json()["lifetime"]["pages"]


# ── the reader's clock does not decide (review round) ────────────────────────

def test_a_reader_clock_ahead_keeps_each_sitting_in_its_own_window(db, client, acc, book):
    """The in-book sync flushes the sitting and sends the snapshot seconds
    later; a reader clock 10 minutes ahead dates the sitting after the
    snapshot. Arrival order still puts it in its window."""
    _snap(client, acc, 100)
    _age(db, 24)
    _sess(client, acc, _sid(1), 20, 1800)      # yesterday's sitting
    _snap(client, acc, 160)
    assert _pages(db, _sid(1)) == [(60, True)]
    _age(db, 18)
    _sess(client, acc, _sid(2), 10, 1800)
    _sess(client, acc, _sid(3), -8 / 60, 1800)  # ended 2 min ago, reader clock 10 min ahead
    _snap(client, acc, 220)
    assert _pages(db, _sid(2), _sid(3)) == [(30, True), (30, True)]
    assert _row(db).pages_pending == 0
    assert _own(client, book)["pages_turned"] == 220


def test_a_reader_clock_behind_keeps_each_sitting_in_its_own_window(db, client, acc, book):
    """A reader clock 3 hours behind dates a sitting that just ended before
    the previous snapshot; it was received after it, so it takes this growth,
    and an older sitting received before that snapshot does not."""
    _sess(client, acc, _sid(1), 30, 1200)       # received before the base snapshot
    _snap(client, acc, 100)
    _age(db, 2)
    _sess(client, acc, _sid(2), 3, 1200)        # really just now; its clock says 3 h ago
    _snap(client, acc, 125)
    assert _pages(db, _sid(1), _sid(2)) == [(None, False), (25, True)]
    assert _own(client, book)["pages_turned"] == 125


def test_a_session_trimmed_before_attribution_weighs_its_trimmed_time(db, client, acc, book):
    """3600 s trimmed to 600 s plus a 600 s sitting: the growth splits evenly,
    and the book's pace stays the reader's own (170 pages over 5100 s)."""
    _snap(client, acc, 100)                     # 3000 s
    _age(db, 10)
    _sess(client, acc, _sid(1), 6, 3600)
    _sess(client, acc, _sid(2), 3, 600)
    sid = _reading(db, _sid(1)).id
    assert client.patch(f"/api/stats/sessions/{sid}", json={"duration_seconds": 600}).status_code == 200
    _snap(client, acc, 170)                     # 5100 s
    assert _pages(db, _sid(1), _sid(2)) == [(35, True), (35, True)]
    own = _own(client, book)
    assert own["pages_turned"] == 170
    assert own["pace_pages_per_min"] == pytest.approx(2.0)


def test_a_trim_after_attribution_keeps_the_readers_pace(db, client, acc, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(1), 6, 3600)
    _sess(client, acc, _sid(2), 3, 600)
    _snap(client, acc, 170)
    assert _pages(db, _sid(1), _sid(2)) == [(60, True), (10, True)]
    sid = _reading(db, _sid(1)).id
    assert client.patch(f"/api/stats/sessions/{sid}", json={"duration_seconds": 600}).status_code == 200
    own = _own(client, book)
    assert own["pages_turned"] == 170
    assert own["pace_pages_per_min"] == pytest.approx(2.0)


def test_a_superseded_session_gives_its_pages_back_to_the_counter(db, client, acc, user, book):
    """Imported KOReader history covering the same sitting takes the session
    out of Tome's totals; its attributed pages then count with the counter."""
    _snap(client, acc, 100)
    _age(db, 10)
    start = _sess(client, acc, _sid(1), 5, 1800)
    _snap(client, acc, 140)
    assert _pages(db, _sid(1)) == [(40, True)]
    db.add(PageStat(user_id=user.id, book_id=book.id, page=5, total_pages=300,
                    start_time=start + 60, duration_seconds=30, device="kindle"))
    db.flush()
    # The counter's 140, plus the one page the imported history describes.
    assert _own(client, book)["pages_turned"] == 141
    assert _per_book(client, 0)[book.id] == 141
    assert _lifetime(client) == 141


def test_lifetime_pages_follow_the_counters_through_a_delete_and_a_resync(db, client, acc, book):
    _snap(client, acc, 100)
    _snap(client, acc, 20, device_id=DEVICE2)
    _age(db, 10)
    _age(db, 10, device_id=DEVICE2)
    start = _sess(client, acc, _sid(1), 8, 1200)
    _snap(client, acc, 130)
    _age(db, 6)
    _sess(client, acc, _sid(2), 4, 600, device_id=DEVICE2)
    _snap(client, acc, 35, device_id=DEVICE2)
    assert _lifetime(client) == 130 + 35
    sid = _reading(db, _sid(1)).id
    assert client.delete(f"/api/stats/sessions/{sid}").status_code == 200
    assert _lifetime(client) == 130 + 35
    _sess(client, acc, _sid(1), 0, 1200, start=start)    # the reader sends it again
    _sess(client, acc, _sid(3), 2, 600)
    _snap(client, acc, 150)
    assert _pages(db, _sid(3)) == [(20, True)]
    assert _own(client, book)["pages_turned"] == 150 + 35
    assert _per_book(client, 0)[book.id] == 185
    assert _lifetime(client) == 185


def test_an_unreadable_counter_between_two_snapshots(db, client, acc, book):
    """A window next to a counter Tome could not read is never attributed:
    its sessions are closed with no pages and the totals stay the counter."""
    _snap(client, acc, 100)
    _age(db, 10)
    _sess(client, acc, _sid(1), 8, 600)
    assert _put_books(client, acc, _item(pages="x", seconds=3000)).status_code == 200
    assert _row(db).pages is None
    _age(db, 5)
    _sess(client, acc, _sid(2), 2, 600)
    _snap(client, acc, 150)
    assert _pages(db, _sid(1), _sid(2)) == [(0, True), (0, True)]
    assert _own(client, book)["pages_turned"] == 150
    _age(db, 1)
    _sess(client, acc, _sid(3), 0.5, 600)
    _snap(client, acc, 160)
    assert _pages(db, _sid(3)) == [(10, True)]
    assert _own(client, book)["pages_turned"] == 160


def test_pending_then_reset_then_growth(db, client, acc, book):
    _snap(client, acc, 100)
    _age(db, 10)
    _snap(client, acc, 130)          # pending 30
    _age(db, 3)
    _sess(client, acc, _sid(1), 2, 600)
    _snap(client, acc, 5)            # reset: the pending pages go with the old counter
    assert _row(db).counter_epoch == 1 and _row(db).pages_pending == 0
    _age(db, 1)
    _sess(client, acc, _sid(2), 0.5, 600)
    _snap(client, acc, 12)
    assert _pages(db, _sid(1), _sid(2)) == [(0, True), (7, True)]
    assert _own(client, book)["pages_turned"] == 12


def test_two_users_on_the_same_device_and_document(db, client, acc, user, book, make_book):
    other = _member(db)
    acc2 = _account(db, other, username="reader2")
    b2 = make_book(title="Other")
    _link(db, other, b2)
    _snap(client, acc, 100)
    _snap(client, acc2, 10)
    db.expire_all()
    for r in db.query(KOSyncBookStats).all():
        r.updated_at = datetime.utcnow() - timedelta(hours=10)
    db.flush()
    _sess(client, acc, _sid(1), 5, 1800)
    _sess(client, acc2, _sid(2), 5, 1800)
    assert _put_books(client, acc2, _item(pages=30, seconds=900)).status_code == 200
    assert _put_books(client, acc, _item(pages=160, seconds=4800)).status_code == 200
    db.expire_all()
    got = {r.user_id: (r.reading_session.pages_turned, r.reading_session.pages_attributed)
           for r in db.query(KOSyncSessionReceipt).all()}
    assert got == {user.id: (60, True), other.id: (20, True)}
    assert _own(client, book)["pages_turned"] == 160
    assert _own(client, b2, headers=_jwt(other))["pages_turned"] == 30
    assert _lifetime(client) == 160
    assert _lifetime(client, _jwt(other)) == 30


# ── the startup migration ────────────────────────────────────────────────────

def test_the_startup_migration_adds_the_columns_with_their_defaults():
    """Tables as a database from before these columns has them (reading
    sessions as in 3.0.0, the reader-stats tables as on this branch before):
    the migration adds every column, fills received_at, and runs twice."""
    from sqlalchemy import create_engine, text

    from backend.main import migrate_reader_page_attribution

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE reading_sessions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "book_id INTEGER, started_at DATETIME NOT NULL, ended_at DATETIME, duration_seconds INTEGER, "
            "progress_start FLOAT, progress_end FLOAT, pages_turned INTEGER, device VARCHAR(255), "
            "session_uuid VARCHAR(36))"))
        conn.execute(text(
            "CREATE TABLE kosync_book_stats (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "device_id VARCHAR(64) NOT NULL, document VARCHAR(32) NOT NULL, pages INTEGER, seconds INTEGER, "
            "snapshot TEXT NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(text(
            "CREATE TABLE kosync_session_receipts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "device_id VARCHAR(64) NOT NULL, session_uuid VARCHAR(64) NOT NULL, document VARCHAR(64) NOT NULL, "
            "started_at DATETIME NOT NULL, ended_at DATETIME NOT NULL, seconds INTEGER NOT NULL, "
            "reading_session_id INTEGER, updated_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO reading_sessions (id, user_id, started_at, pages_turned) "
                          "VALUES (1, 1, '2026-11-01 10:00:00', 12)"))
        conn.execute(text("INSERT INTO kosync_book_stats (user_id, device_id, document, pages, snapshot, updated_at) "
                          "VALUES (1, 'D', 'doc', 50, '{}', '2026-11-01 11:00:00')"))
        conn.execute(text(
            "INSERT INTO kosync_session_receipts (user_id, device_id, session_uuid, document, started_at, "
            "ended_at, seconds, reading_session_id, updated_at) VALUES (1, 'D', 's', 'doc', "
            "'2026-11-01 10:00:00', '2026-11-01 10:20:00', 1200, 1, '2026-11-01 10:21:00')"))
        conn.commit()

        migrate_reader_page_attribution(conn)
        migrate_reader_page_attribution(conn)   # idempotent

        def info(table):
            return {r[1]: (r[2], r[3], r[4]) for r in conn.execute(text(f"PRAGMA table_info({table})"))}

        assert info("reading_sessions")["pages_attributed"] == ("BOOLEAN", 1, "0")
        kbs = info("kosync_book_stats")
        assert {c: kbs[c] for c in ("counter_epoch", "pages_attributed", "pages_pending", "pending_since")} == {
            "counter_epoch": ("INTEGER", 1, "0"), "pages_attributed": ("INTEGER", 1, "0"),
            "pages_pending": ("INTEGER", 1, "0"), "pending_since": ("DATETIME", 0, None)}
        ksr = info("kosync_session_receipts")
        assert {c: ksr[c][0] for c in ("pages_epoch", "attributed_seconds", "received_at")} == {
            "pages_epoch": "INTEGER", "attributed_seconds": "INTEGER", "received_at": "DATETIME"}
        assert conn.execute(text("SELECT pages_turned, pages_attributed FROM reading_sessions")).one() == (12, 0)
        assert conn.execute(text(
            "SELECT counter_epoch, pages_attributed, pages_pending, pending_since FROM kosync_book_stats")).one() == (
            0, 0, 0, None)
        assert conn.execute(text(
            "SELECT received_at, pages_epoch, attributed_seconds FROM kosync_session_receipts")).one() == (
            "2026-11-01 10:21:00", None, None)
        # An older Tome's insert, naming only its own columns, gets the default.
        conn.execute(text("INSERT INTO reading_sessions (user_id, started_at) VALUES (1, '2026-11-02 10:00:00')"))
        assert conn.execute(text("SELECT pages_attributed FROM reading_sessions WHERE id = 2")).scalar() == 0
