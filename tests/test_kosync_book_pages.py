"""Per-book page counts from a KOSync reader's own stats (CrossInk 2.0+).

CrossInk uploads reading sessions with time but no pages, and separately a
lifetime snapshot per book (``PUT .../stats/books``) that holds the reader's
own page counter (forward turns) and its own reading seconds. Tome keeps the
latest snapshot per device and document and adds its pages to a book's
all-time pages; pace divides the reader's pages by the reader's seconds only.
A single snapshot changes nothing with a date (a day, a session, a 30-day
window): the reader never says when its pages were turned. The growth
between two snapshots is attributed to sessions; see
tests/test_kosync_attributed_pages.py.

The reply to the upload is a hard contract with the firmware
(StatsUploadClient::put); ``_delivered`` below is the same check as in
tests/test_kosync_stats.py.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.book import BOOK_NO_FILE, Book
from backend.models.ko_stats import KoHash
from backend.models.kosync import KOSyncBookStats, KOSyncDocumentMap, KOSyncUser
from backend.models.tome_sync import ReadingSession
from backend.models.user import User

BASE = "/api/v1/api/v1"
DEVICE = "A1B2C3D4E5F6"
DEVICE2 = "0F0E0D0C0B0A"
DOC = "0123456789abcdef0123456789abcdef"
DOC2 = "fedcba9876543210fedcba9876543210"


# ── helpers ───────────────────────────────────────────────────────────────────

def _account(db, user, username=None):
    acc = KOSyncUser(username=username or user.username, userkey="k" * 32, user_id=user.id)
    db.add(acc)
    db.flush()
    return acc


def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _jwt(user):
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def _member(db, name="reader2"):
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
                is_active=True, role="member", must_change_password=False)
    db.add(user)
    db.flush()
    return user


def _link(db, user, book, document=DOC):
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=document, book_id=book.id))
    db.flush()


def _item(document=DOC, pages=300, seconds=6000, **overrides):
    """One item as StatsUploadPayload::BookBatch::add writes it."""
    item = {
        "document": document, "v": 5, "sessions": 9, "seconds": seconds, "pages": pages,
        "completed": False, "avg_fwd": 18, "pace_n": 250, "eta": 5400,
        "start_manual": False, "finish_manual": False, "start_date": 1761955200, "finished_date": 0,
        "tod": [0, 3000, 2000, 1000], "dow": [0, 0, 1200, 0, 2000, 1800, 1000],
    }
    item.update(overrides)
    return item


def _put_books(client, acc, *items, device_id=DEVICE):
    return client.put(f"{BASE}/stats/books", headers=_headers(acc),
                      json={"device_id": device_id, "items": list(items)})


def _delivered(response, *, book_count):
    """StatsUploadClient::put for a book batch: 200, a uint ``until`` and
    ``accepted`` equal to the number of books sent."""
    if response.status_code != 200:
        return False
    reply = response.json()

    def uint(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    return uint(reply.get("until")) and uint(reply.get("accepted")) and reply["accepted"] == book_count


def _session(client, acc, document=DOC, seconds=3600, start=None, session_id="a" * 32):
    """A CrossInk reading session: time, no pages."""
    start = int(time.time()) - 3 * 86400 if start is None else start
    r = client.put(f"{BASE}/stats/sessions", headers=_headers(acc), json={
        "device_id": DEVICE, "items": [{
            "session_id": session_id, "document": document, "started_at": start,
            "ended_at": start + seconds, "seconds": seconds, "utc_offset_minutes": 0,
        }],
    })
    assert r.status_code == 200, r.text
    return start


def _own(client, book, headers=None):
    r = client.get(f"/api/books/{book.id}/reading-stats", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["own"]


def _per_book(client, days, headers=None):
    data = client.get(f"/api/stats?days={days}", headers=headers or {}).json()
    return {row["book_id"]: row["pages_turned"] for row in data["per_book_time"]}


@pytest.fixture()
def user(admin_user):
    return admin_user[0]


@pytest.fixture()
def acc(db, user):
    return _account(db, user)


# ── storing the snapshot ──────────────────────────────────────────────────────

def test_a_snapshot_is_stored_and_a_lower_one_replaces_it(db, client, acc, user):
    assert _delivered(_put_books(client, acc, _item(pages=300, seconds=6000)), book_count=1)
    row = db.query(KOSyncBookStats).one()
    assert (row.user_id, row.device_id, row.document, row.pages, row.seconds) == (
        user.id, DEVICE, DOC, 300, 6000)
    assert json.loads(row.snapshot) == _item(pages=300, seconds=6000)

    # The reader's stats were reset (or its card restored): its current truth.
    assert _delivered(_put_books(client, acc, _item(pages=12, seconds=240, sessions=1)), book_count=1)
    db.expire_all()
    row = db.query(KOSyncBookStats).one()
    assert (row.pages, row.seconds) == (12, 240)
    assert json.loads(row.snapshot)["sessions"] == 1


def test_the_document_is_stored_lower_case_once_per_device(db, client, acc):
    _put_books(client, acc, _item(document=DOC.upper(), pages=10))
    _put_books(client, acc, _item(document=DOC, pages=20))
    _put_books(client, acc, _item(pages=5), device_id=DEVICE2)
    rows = {(r.device_id, r.document): r.pages for r in db.query(KOSyncBookStats)}
    assert rows == {(DEVICE, DOC): 20, (DEVICE2, DOC): 5}


# ── the reply contract ───────────────────────────────────────────────────────

def test_the_reply_is_unchanged(db, client, acc):
    """Same keys and values as before snapshots were kept: ``accepted`` is
    the item count and ``accepted_daily`` stays 0 (per-book days are not
    stored, so they must never be marked delivered)."""
    r = _put_books(client, acc, _item())
    assert _delivered(r, book_count=1)
    assert set(r.json()) == {"until", "accepted", "accepted_daily"}
    assert r.json()["accepted_daily"] == 0

    batch = [_item(document=f"{i:032x}", pages=i) for i in range(1, 4)]
    batch[0]["daily"] = [{"date": "2026-11-09", "seconds": 900}]
    r = _put_books(client, acc, *batch)
    assert _delivered(r, book_count=3) and r.json()["accepted_daily"] == 0
    assert db.query(KOSyncBookStats).count() == 4


def test_a_document_twice_in_one_batch_counts_every_item(db, client, acc):
    """``accepted`` must equal what the reader sent, or it retries forever."""
    r = _put_books(client, acc, _item(pages=10), _item(document=DOC.upper(), pages=11))
    assert _delivered(r, book_count=2)
    assert db.query(KOSyncBookStats).one().pages == 11


def test_items_tome_cannot_read_are_still_accepted(db, client, acc):
    """Today Tome acknowledges any item with a valid document; refusing one
    now would block the reader's queue. Bad counters are stored as unknown."""
    r = _put_books(client, acc, {"document": DOC},
                   _item(document=DOC2, pages="many", seconds=-1))
    assert _delivered(r, book_count=2)
    rows = {r.document: (r.pages, r.seconds) for r in db.query(KOSyncBookStats)}
    assert rows == {DOC: (None, None), DOC2: (None, None)}
    for pages in (True, 2**32):
        _put_books(client, acc, _item(pages=pages))
        db.expire_all()
        assert db.query(KOSyncBookStats).filter_by(document=DOC).one().pages is None


def test_refusals_are_unchanged_and_write_nothing(db, client, acc, user):
    assert _put_books(client, acc).status_code == 400
    assert _put_books(client, acc, _item(document="nope")).status_code == 400
    assert _put_books(client, acc, _item(), device_id="bad id").status_code == 400
    unlinked = KOSyncUser(username="nobody", userkey="k" * 32, user_id=None)
    db.add(unlinked)
    db.flush()
    assert client.put(f"{BASE}/stats/books", headers=_headers(unlinked),
                      json={"device_id": DEVICE, "items": [_item()]}).status_code == 404
    assert db.query(KOSyncBookStats).count() == 0


# ── pages and pace on a book ─────────────────────────────────────────────────

def test_reader_pages_fill_the_book_and_pace_uses_the_readers_own_time(db, client, acc, user, make_book):
    book = make_book(title="Read On The X3")
    _link(db, user, book)
    _session(client, acc, seconds=3600)
    before = _own(client, book)
    assert before["pages_turned"] == 0 and before["pace_pages_per_min"] is None

    _put_books(client, acc, _item(pages=300, seconds=6000))
    own = _own(client, book)
    assert own["pages_turned"] == 300
    # 300 pages over the reader's 100 minutes, not over Tome's 60 session minutes.
    assert own["pace_pages_per_min"] == pytest.approx(3.0)
    # Time, sessions and every dated figure stay Tome's own.
    assert own["total_seconds"] == before["total_seconds"] == 3600
    assert own["sessions"] == before["sessions"]
    assert own["session_timeline"] == before["session_timeline"]
    assert all(day["pages"] == 0 for day in own["session_timeline"])
    assert own["momentum"] == before["momentum"]


def test_reader_pages_add_to_tome_pages_each_with_its_own_time(db, client, acc, user, make_book):
    book = make_book(title="Two Sources")
    _link(db, user, book)
    start = datetime.utcnow() - timedelta(days=2)
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=start,
                          ended_at=start + timedelta(hours=1), duration_seconds=3600,
                          pages_turned=60, device="Kindle"))
    db.flush()
    _session(client, acc, seconds=1800)   # CrossInk time, no pages
    _put_books(client, acc, _item(pages=300, seconds=6000))

    own = _own(client, book)
    assert own["pages_turned"] == 360
    # (60 + 300) pages / (60 + 100) minutes; CrossInk's 30 session minutes are in neither.
    assert own["pace_pages_per_min"] == pytest.approx(2.25)
    assert own["total_seconds"] == 5400


def test_reader_pages_without_reader_time_do_not_change_the_pace(db, client, acc, user, make_book):
    book = make_book(title="No Seconds")
    _link(db, user, book)
    start = datetime.utcnow() - timedelta(days=2)
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=start,
                          ended_at=start + timedelta(hours=1), duration_seconds=3600, pages_turned=60))
    db.flush()
    _put_books(client, acc, _item(pages=40, seconds=0))
    own = _own(client, book)
    assert own["pages_turned"] == 100
    assert own["pace_pages_per_min"] == pytest.approx(1.0)


def test_two_devices_add_up(db, client, acc, user, make_book):
    book = make_book(title="Two Readers")
    _link(db, user, book)
    _put_books(client, acc, _item(pages=100, seconds=1200))
    _put_books(client, acc, _item(pages=50, seconds=600), device_id=DEVICE2)
    own = _own(client, book)
    assert own["pages_turned"] == 150
    assert own["pace_pages_per_min"] == pytest.approx(5.0)


def test_two_documents_of_one_book_add_up(db, client, acc, user, make_book):
    """The reader keeps separate stats per file, e.g. the raw file and a
    copy Tome served with its metadata baked in."""
    book = make_book(title="Two Files")
    _link(db, user, book, DOC)
    _link(db, user, book, DOC2)
    _put_books(client, acc, _item(DOC, pages=100, seconds=3000), _item(DOC2, pages=20, seconds=600))
    own = _own(client, book)
    assert own["pages_turned"] == 120
    assert own["pace_pages_per_min"] == pytest.approx(2.0)


def test_a_snapshot_waits_for_its_link_and_follows_a_relink(db, client, user, make_book):
    acc = _account(db, user, username=user.username)   # link-kosync finds it by name
    first, second = make_book(title="First"), make_book(title="Second")
    _put_books(client, acc, _item(pages=80, seconds=2400))
    assert _own(client, first)["pages_turned"] == 0

    # A document Tome knows only from a snapshot can be linked by hand.
    r = client.post(f"/api/books/{first.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    assert _own(client, first)["pages_turned"] == 80

    r = client.post(f"/api/books/{second.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    assert _own(client, first)["pages_turned"] == 0
    second_own = _own(client, second)
    assert second_own["pages_turned"] == 80
    assert second_own["pace_pages_per_min"] == pytest.approx(2.0)


def test_a_file_tome_served_is_linked_by_its_hash(db, client, acc, make_book):
    book = make_book(title="Served")
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="baked"))
    db.flush()
    _put_books(client, acc, _item(pages=42, seconds=1260))
    assert _own(client, book)["pages_turned"] == 42


def test_a_file_less_book_gets_its_readers_pages(db, client, acc, user):
    book = Book(title="Sideloaded", status=BOOK_NO_FILE, added_by=user.id)
    db.add(book)
    db.flush()
    _link(db, user, book)
    _put_books(client, acc, _item(pages=64, seconds=1920))
    own = _own(client, book)
    assert own["pages_turned"] == 64
    assert own["pace_pages_per_min"] == pytest.approx(2.0)


def test_another_users_snapshots_never_count(db, client, acc, user, make_book):
    book = make_book(title="Shared Book")
    member = _member(db)
    macc = _account(db, member)
    _link(db, member, book)
    _put_books(client, macc, _item(pages=500, seconds=6000))

    # The admin has no snapshot of their own: nothing, even with the same document linked.
    assert _own(client, book)["pages_turned"] == 0
    _link(db, user, book)
    assert _own(client, book)["pages_turned"] == 0
    _put_books(client, acc, _item(pages=30, seconds=600))
    admin_view = client.get(f"/api/books/{book.id}/reading-stats").json()
    assert admin_view["own"]["pages_turned"] == 30
    # The admin aggregate carries no pages, before or after.
    assert "pages_turned" not in admin_view["aggregate"]

    member_own = _own(client, book, headers=_jwt(member))
    assert member_own["pages_turned"] == 500
    assert member_own["pace_pages_per_min"] == pytest.approx(5.0)


def test_a_snapshot_never_counts_through_another_users_link(db, client, acc, user, make_book):
    """The admin's reader sent pages for a document only a member linked:
    they count nowhere for the admin until the admin links it."""
    book = make_book(title="Member's Link")
    member = _member(db)
    _link(db, member, book)
    _put_books(client, acc, _item(pages=90, seconds=1800))
    assert _own(client, book)["pages_turned"] == 0
    assert _own(client, book, headers=_jwt(member))["pages_turned"] == 0
    _link(db, user, book)
    assert _own(client, book)["pages_turned"] == 90


def test_a_member_cannot_reach_another_users_file_less_book(db, client, user):
    book = Book(title="Admin's own reading", status=BOOK_NO_FILE, added_by=user.id)
    db.add(book)
    db.flush()
    member = _member(db)
    macc = _account(db, member)
    _link(db, member, book)
    _put_books(client, macc, _item(pages=500))
    assert client.get(f"/api/books/{book.id}/reading-stats", headers=_jwt(member)).status_code == 404
    assert _own(client, book)["pages_turned"] == 0


# ── the stats page ───────────────────────────────────────────────────────────

def test_the_per_book_table_counts_reader_pages_for_all_time_only(db, client, acc, user, make_book):
    book = make_book(title="Table")
    _link(db, user, book)
    _session(client, acc, seconds=3600)
    _put_books(client, acc, _item(pages=300, seconds=6000))
    assert _per_book(client, 0) == {book.id: 300}
    assert _per_book(client, 30) == {book.id: 0}
    assert _per_book(client, 365) == {book.id: 0}


def test_the_per_book_table_never_shows_another_users_pages(db, client, acc, user, make_book):
    book = make_book(title="Table Two")
    member = _member(db)
    macc = _account(db, member)
    _link(db, member, book)
    _link(db, user, book)
    _session(client, acc, seconds=3600)
    _put_books(client, macc, _item(pages=500))
    assert _per_book(client, 0) == {book.id: 0}


def test_dated_figures_stay_without_reader_pages(db, client, acc, user, make_book):
    """The reader never says when its pages were turned, so no day, session,
    window or total across books takes them."""
    book = make_book(title="Windows")
    _link(db, user, book)
    start = _session(client, acc, seconds=3600)

    def snapshot():
        data = client.get("/api/stats?days=30").json()
        alltime = client.get("/api/stats?days=0").json()
        day = datetime.fromtimestamp(start, timezone.utc).date().isoformat()
        sessions = client.get("/api/stats/sessions").json()
        sessions = sessions["sessions"] if isinstance(sessions, dict) else sessions
        return {
            "headline": data["headline"]["pages_turned"],
            "daily": [d["pages"] for d in data["daily"]],
            "alltime_headline": alltime["headline"]["pages_turned"],
            "pace_list": data["reading_pace"],
            "pace_by_format": data["pace_by_format"],
            "home": client.get("/api/home/stats").json()["pages_turned_30d"],
            "calendar_day": client.get(f"/api/stats/calendar/day?day={day}").json()["pages"],
            "session_rows": [row.get("pages_turned") for row in sessions],
        }

    before = snapshot()
    _put_books(client, acc, _item(pages=300, seconds=6000))
    assert snapshot() == before
    assert before["calendar_day"] == 0 and before["headline"] == 0


def test_all_time_lifetime_pages_include_reader_pages(db, client, acc, user, make_book):
    """The all-time lifetime total matches the all-time per-book table: it
    counts the reader's own pages too, on books the user can see."""
    book = make_book(title="Lifetime")
    _link(db, user, book)
    _session(client, acc, seconds=3600)
    assert client.get("/api/stats?days=0").json()["lifetime"]["pages"] == 0
    _put_books(client, acc, _item(pages=300, seconds=6000))
    alltime = client.get("/api/stats?days=0").json()
    assert alltime["lifetime"]["pages"] == 300
    assert _per_book(client, 0) == {book.id: 300}
    # A snapshot of a document linked to nothing counts nowhere.
    _put_books(client, acc, _item(DOC2, pages=50))
    assert client.get("/api/stats?days=0").json()["lifetime"]["pages"] == 300


# ── account export ───────────────────────────────────────────────────────────

def test_the_account_export_holds_the_snapshots(db, client, acc, user, make_book):
    _put_books(client, acc, _item(pages=300, seconds=6000))
    member = _member(db)
    _put_books(client, _account(db, member), _item(DOC2, pages=7))
    export = client.get("/api/auth/me/backup").json()["kosync_legacy"]
    assert len(export["book_stats"]) == 1
    entry = export["book_stats"][0]
    assert (entry["device_id"], entry["document"], entry["pages"], entry["seconds"]) == (
        DEVICE, DOC, 300, 6000)
    assert json.loads(entry["snapshot"]) == _item(pages=300, seconds=6000)


def test_a_retried_put_racing_the_first_into_a_new_link_is_not_a_500(db, client, acc, user, make_book,
                                                                       monkeypatch):
    """The first book batch for a file Tome served links its document. A
    reader that timed out retries while the first request runs; the loser hits
    the unique constraint on the link, which is retried like the store."""
    from sqlalchemy.exc import IntegrityError

    from backend.api import kosync_stats as api_module

    book = make_book()
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="raw"))
    db.flush()
    real = api_module._find_or_hash_link
    calls = []

    def racing(db_, user_id, document):
        calls.append(document)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO kosync_document_map", {}, Exception("UNIQUE constraint failed"))
        return real(db_, user_id, document)

    monkeypatch.setattr(api_module, "_find_or_hash_link", racing)
    # Let the handler's rollback undo only what the request did, as in prod.
    savepoint = db.begin_nested()
    monkeypatch.setattr(db, "rollback", lambda: savepoint.rollback() if savepoint.is_active else None)
    r = _put_books(client, acc, _item(pages=120, seconds=2400))
    assert _delivered(r, book_count=1), r.text
    assert set(r.json()) == {"until", "accepted", "accepted_daily"} and r.json()["accepted_daily"] == 0
    assert len(calls) == 2
    assert _own(client, book)["pages_turned"] == 120
