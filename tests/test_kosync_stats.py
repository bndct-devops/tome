"""Reading stats uploaded by a KOSync reader (CrossInk 2.0+ stats API).

The reader is the other half of this contract and Tome cannot be tested
against real hardware in CI, so these tests hold Tome to what the firmware
does with each reply (CrossInk ``src/network/StatsUploadClient.cpp`` and
``ReadingSyncUpload.cpp``, v2.0.0-rc and development):

- ``_delivered`` is the firmware's own acceptance check. An upload the
  firmware does not consider delivered is retried forever; one it does
  consider delivered is deleted from the device. Both directions matter.
- the routes live under ``/api/v1/api/v1/stats`` because the firmware appends
  ``/api/v1/stats/...`` to the KOSync server URL, which for Tome ends in
  ``/api/v1``.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.security import hash_password
from backend.models.ko_stats import KoHash
from backend.models.kosync import (
    KOSyncDailyStats,
    KOSyncDeviceStats,
    KOSyncDocumentMap,
    KOSyncSessionReceipt,
    KOSyncUser,
)
from backend.models.library import Library
from backend.models.tome_sync import ReadingSession
from backend.models.user import User

BASE = "/api/v1/api/v1"
DEVICE = "A1B2C3D4E5F6"
DOC = "0123456789abcdef0123456789abcdef"
DOC2 = "fedcba9876543210fedcba9876543210"
SID = "a" * 32
SID2 = "b" * 32


# ── helpers ───────────────────────────────────────────────────────────────────

def _account(db, tome_user, username=None, linked=True):
    acc = KOSyncUser(
        username=username or f"ks-{tome_user.username}", userkey="k" * 32,
        user_id=tome_user.id if linked else None,
    )
    db.add(acc)
    db.flush()
    return acc


def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _other_user(db, name="reader2"):
    user = User(
        username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
        is_active=True, role="member", must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def _global(daily=None, **overrides):
    """The body CrossInk builds in StatsUploadPayload::global."""
    body = {
        "device_id": DEVICE, "device": "CrossInk", "v": 5,
        "sessions": 110, "seconds": 65820, "pages": 2793, "completed": 0,
        "tod": [6540, 21660, 14340, 23280],
        "dow": [10320, 11760, 10680, 9180, 6660, 10920, 6300],
        "anchor_day": 9809, "streak": 12, "history_b64": "A" * 123 + "=",
    }
    body.update(overrides)
    if daily is not None:
        body["daily"] = daily
    return body


def _session(session_id=SID, document=DOC, start=None, seconds=600, span=None, **overrides):
    start = int(time.time()) - 7200 if start is None else start
    item = {
        "session_id": session_id, "document": document, "started_at": start,
        "ended_at": start + (seconds if span is None else span), "seconds": seconds,
        "utc_offset_minutes": 60,
    }
    item.update(overrides)
    return item


def _put_sessions(client, acc, *items, device_id=DEVICE):
    return client.put(f"{BASE}/stats/sessions", headers=_headers(acc),
                      json={"device_id": device_id, "items": list(items)})


def _delivered(response, *, session_id=None, book_count=0, daily=False):
    """StatsUploadClient::put: does the firmware treat this upload as delivered?"""
    if response.status_code != 200:
        return False
    reply = response.json()

    def uint(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    if not uint(reply.get("until")):
        return False
    if session_id is not None and reply.get("accepted_session_ids") != [session_id]:
        return False
    if book_count and not (uint(reply.get("accepted")) and reply["accepted"] == book_count):
        return False
    if daily and not (uint(reply.get("accepted_daily")) and reply["accepted_daily"] == 1):
        return False
    return True


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def acc(db, admin_user):
    user, _ = admin_user
    return _account(db, user)


# ── capability probe ──────────────────────────────────────────────────────────

def test_probe_answers_a_json_object(client, acc):
    """Authenticate on the reader: 200 + a body starting with '{' = supported."""
    r = client.get(f"{BASE}/stats/summary", headers=_headers(acc))
    assert r.status_code == 200
    assert r.text.lstrip().startswith("{")
    assert r.json()["devices"] == []


def test_probe_is_404_for_an_unlinked_account(db, client, admin_user):
    """No Tome user behind the KOSync account: the reader must keep its stats."""
    user, _ = admin_user
    unlinked = _account(db, user, username="nobody", linked=False)
    assert client.get(f"{BASE}/stats/summary", headers=_headers(unlinked)).status_code == 404
    for path, body in (
        ("global", _global()),
        ("sessions", {"device_id": DEVICE, "items": [_session()]}),
        ("books", {"device_id": DEVICE, "items": [{"document": DOC}]}),
    ):
        assert client.put(f"{BASE}/stats/{path}", headers=_headers(unlinked), json=body).status_code == 404
    assert db.query(ReadingSession).count() == 0
    assert db.query(KOSyncDeviceStats).count() == 0


def test_probe_needs_the_kosync_credentials(client, acc):
    assert client.get(f"{BASE}/stats/summary").status_code == 401
    bad = {"x-auth-user": acc.username, "x-auth-key": "x" * 32}
    assert client.get(f"{BASE}/stats/summary", headers=bad).status_code == 401


def test_endpoints_tome_does_not_have_answer_404(client, acc):
    """Once the reader sees the stats API it also tries the rest of the
    CrossPoint Sync API. Each of these has a 404 fallback in the firmware
    (one PUT per book, no change feed, streak skipped); an HTML page or a
    200 here would be read as a broken server."""
    h = _headers(acc)
    assert client.put(f"{BASE}/progress/batch", headers=h, json={"items": []}).status_code == 404
    assert client.get(f"{BASE}/progress/changes?since=0&limit=5&device=x", headers=h).status_code == 404
    assert client.get(f"{BASE}/stats/streak?tz=-60", headers=h).status_code == 404


def test_clippings_fail_without_switching_stats_off(client, acc):
    """The firmware sends clippings through the stats client, where a 404 or
    405 on an upload marks the whole server as having no extended API
    (recordMissingApi) and stats stop with it. Any other error only fails the
    clippings step."""
    h = _headers(acc)
    for r in (
        client.put(f"{BASE}/clippings/{DOC}", headers=h, json={"items": [{"id": "x", "deleted": 1}]}),
        client.get(f"{BASE}/clippings/{DOC}?cursor=0&limit=1&format=reader", headers=h),
    ):
        assert r.status_code == 501
        assert r.status_code not in (404, 405)


# ── one whole sync, in the firmware's order ───────────────────────────────────

def test_a_full_sync_is_delivered_step_by_step(db, client, admin_user, acc, make_book):
    """ReadingSyncUpload::globalStats stops at the first step that is not
    delivered, and sessions come last. Then the per-book snapshot."""
    user, _ = admin_user
    book = make_book(title="Read On The X3")
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="baked"))
    db.flush()
    h = _headers(acc)

    assert _delivered(client.put(f"{BASE}/stats/global", headers=h, json=_global()))
    assert _delivered(
        client.put(f"{BASE}/stats/global", headers=h,
                   json=_global(daily=[{"date": "2026-11-09", "seconds": 61}])),
        daily=True,
    )
    assert _delivered(_put_sessions(client, acc, _session()), session_id=SID)
    books = {"device_id": DEVICE, "items": [{"document": DOC, "v": 5, "sessions": 9, "seconds": 8400}]}
    assert _delivered(client.put(f"{BASE}/stats/books", headers=h, json=books), book_count=1)

    session = db.query(ReadingSession).one()
    assert (session.user_id, session.book_id, session.duration_seconds) == (user.id, book.id, 600)
    assert session.device == "CrossInk"
    assert db.query(KOSyncDailyStats).one().seconds == 61


# ── lifetime snapshot + daily seconds ─────────────────────────────────────────

def test_global_snapshot_replaces_the_previous_one(db, client, admin_user, acc):
    user, _ = admin_user
    h = _headers(acc)
    client.put(f"{BASE}/stats/global", headers=h, json=_global())
    client.put(f"{BASE}/stats/global", headers=h, json=_global(sessions=111, seconds=66000))

    row = db.query(KOSyncDeviceStats).one()
    assert (row.user_id, row.device_id, row.device) == (user.id, DEVICE, "CrossInk")
    snap = json.loads(row.snapshot)
    assert snap["seconds"] == 66000 and "daily" not in snap

    summary = client.get(f"{BASE}/stats/summary", headers=h).json()
    assert summary["sessions"] == 111 and summary["seconds"] == 66000
    assert summary["devices"][0]["device"] == "CrossInk"


def test_a_day_sent_again_keeps_the_larger_value(db, client, acc):
    """The device's counter for a day only grows; a stale retry must not undo it."""
    h = _headers(acc)
    for seconds in (900, 1500, 1200):
        r = client.put(f"{BASE}/stats/global", headers=h,
                       json=_global(daily=[{"date": "2026-11-09", "seconds": seconds}]))
        assert _delivered(r, daily=True)
    assert db.query(KOSyncDailyStats).one().seconds == 1500


def test_daily_is_counted_per_distinct_date(db, client, acc):
    r = client.put(f"{BASE}/stats/global", headers=_headers(acc), json=_global(daily=[
        {"date": "2026-11-08", "seconds": 10}, {"date": "2026-11-09", "seconds": 20},
    ]))
    assert r.json()["accepted_daily"] == 2
    assert db.query(KOSyncDailyStats).count() == 2


@pytest.mark.parametrize("daily", [
    [{"date": "09.11.2026", "seconds": 1}],
    [{"date": "1999-12-31", "seconds": 1}],
    [{"date": "2026-11-09", "seconds": -1}],
    [{"date": "2026-11-09", "seconds": "61"}],
    [{"date": "2026-11-09"}],
    "2026-11-09",
])
def test_a_bad_day_is_refused_and_nothing_is_stored(db, client, acc, daily):
    r = client.put(f"{BASE}/stats/global", headers=_headers(acc), json=_global(daily=daily))
    assert r.status_code == 400
    assert db.query(KOSyncDailyStats).count() == 0
    assert db.query(KOSyncDeviceStats).count() == 0


def test_global_needs_a_device_id(client, acc):
    body = _global()
    del body["device_id"]
    assert client.put(f"{BASE}/stats/global", headers=_headers(acc), json=body).status_code == 400
    assert client.put(f"{BASE}/stats/global", headers=_headers(acc),
                      json=_global(device_id="no spaces")).status_code == 400


# ── sessions ──────────────────────────────────────────────────────────────────

def test_a_session_becomes_a_reading_session(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    book = make_book(title="Linked")
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="raw"))
    db.flush()
    start = int(time.time()) - 3600

    r = _put_sessions(client, acc, _session(start=start, seconds=600, span=900))
    assert _delivered(r, session_id=SID)

    s = db.query(ReadingSession).one()
    assert s.user_id == user.id and s.book_id == book.id
    assert s.started_at == _utc(start) and s.ended_at == _utc(start + 900)
    assert s.duration_seconds == 600
    receipt = db.query(KOSyncSessionReceipt).one()
    assert (receipt.session_uuid, receipt.document, receipt.reading_session_id) == (SID, DOC, s.id)
    assert s.pages_turned is None and s.progress_end is None
    # ... and it is real reading time on the book's page.
    stats = client.get(f"/api/books/{book.id}/reading-stats").json()["own"]
    assert stats["sessions"] == 1 and stats["total_seconds"] == 600
    # The calendar and the session log read the same table.
    months = {_utc(start - 86400).strftime("%Y-%m"), _utc(start).strftime("%Y-%m")}
    days = [d for m in months for d in client.get(f"/api/stats/calendar?month={m}").json()["days"]]
    assert sum(d["seconds"] for d in {d["date"]: d for d in days}.values()) == 600
    listed = client.get("/api/stats/sessions").json()
    listed = listed["sessions"] if isinstance(listed, dict) else listed
    assert [row["duration_seconds"] for row in listed] == [600]


def test_a_growing_session_updates_its_row(db, client, acc):
    """The reader checkpoints a long sitting under one id; totals only grow."""
    start = int(time.time()) - 7200
    for seconds in (300, 900, 1800):
        assert _delivered(_put_sessions(client, acc, _session(start=start, seconds=seconds)),
                          session_id=SID)
    s = db.query(ReadingSession).one()
    assert s.duration_seconds == 1800 and s.ended_at == _utc(start + 1800)

    # A stale retry of an older checkpoint is acknowledged and changes nothing.
    assert _delivered(_put_sessions(client, acc, _session(start=start, seconds=900)), session_id=SID)
    db.expire_all()
    s = db.query(ReadingSession).one()
    assert s.duration_seconds == 1800 and s.ended_at == _utc(start + 1800)


def test_the_same_id_twice_in_one_request_is_one_session(db, client, acc):
    start = int(time.time()) - 7200
    r = _put_sessions(client, acc, _session(start=start, seconds=300), _session(start=start, seconds=700))
    assert r.status_code == 200
    assert db.query(ReadingSession).one().duration_seconds == 700


def test_a_session_for_an_unknown_book_is_kept_and_linked_later(db, client, admin_user, acc, make_book):
    """Refusing it would block the reader's queue behind it, and the reader
    deletes it once acknowledged, so it is stored with its hash and picks up
    its book when the document is identified."""
    user, _ = admin_user
    assert _delivered(_put_sessions(client, acc, _session()), session_id=SID)
    s = db.query(ReadingSession).one()
    assert s.book_id is None

    book = make_book(title="Arrives Later")
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="raw"))
    db.flush()
    r = client.put("/api/v1/syncs/progress", headers=_headers(acc), json={
        "document": DOC, "percentage": 0.3, "progress": "/body/x[1]", "device": "CrossInk",
    })
    assert r.status_code == 200
    db.expire_all()
    assert db.query(ReadingSession).one().book_id == book.id


def test_a_manual_link_moves_the_documents_sessions(db, client, admin_user, make_book):
    user, _ = admin_user
    acc = _account(db, user, username=user.username)   # the link endpoint finds it by name
    wrong, right = make_book(title="Wrong"), make_book(title="Right")
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=DOC, book_id=wrong.id))
    db.flush()
    _put_sessions(client, acc, _session())
    client.put("/api/v1/syncs/progress", headers=_headers(acc), json={
        "document": DOC, "percentage": 0.3, "progress": "/body/x[1]", "device": "CrossInk",
    })
    assert db.query(ReadingSession).one().book_id == wrong.id

    r = client.post(f"/api/books/{right.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(ReadingSession).one().book_id == right.id


def test_sessions_take_the_device_name_from_the_snapshot(db, client, acc):
    _put_sessions(client, acc, _session(session_id=SID))
    client.put(f"{BASE}/stats/global", headers=_headers(acc), json=_global(device="CrossInk X3"))
    _put_sessions(client, acc, _session(session_id=SID2, document=DOC2))
    names = {r.session_uuid: r.reading_session.device for r in db.query(KOSyncSessionReceipt)}
    assert names == {SID: "KOSync reader", SID2: "CrossInk X3"}


_NOW = int(time.time())
_OLD = _NOW - 30 * 86400


@pytest.mark.parametrize("change", [
    {"seconds": 9},                                             # under the firmware's 10 s floor
    {"seconds": 601},                                           # more reading than wall-clock time
    {"ended_at": _OLD + 7 * 86400 + 1},                         # longer than a week
    {"ended_at": _OLD - 1},                                     # ends before it starts
    {"session_id": "x" * 65},                                   # longer than an id may be
    {"session_id": "has space"},
    {"document": "not-a-hash"},
    {"started_at": 100},                                        # before 2000
    {"seconds": "600"},
    {"started_at": _NOW + 2 * 86400, "ended_at": _NOW + 2 * 86400 + 600},  # a clock far ahead
], ids=lambda c: ",".join(c))
def test_an_unusable_session_is_refused(db, client, acc, change):
    item = _session(start=_OLD, seconds=600)
    item.update(change)
    r = _put_sessions(client, acc, item)
    assert r.status_code == 400, r.text
    assert db.query(ReadingSession).count() == 0


def test_what_the_firmware_calls_valid_is_never_refused(db, client, acc):
    """ReadingSessionJournal::valid: the edges of what a reader can send."""
    now = int(time.time())
    edges = [
        _session(session_id="1" * 32, start=now - 20, seconds=10),             # shortest
        _session(session_id="2" * 32, start=now - 7 * 86400, seconds=10, span=7 * 86400),  # longest span
        _session(session_id="3" * 32, start=now - 600, seconds=600),           # no idle time at all
        _session(session_id="4" * 32, start=now - 60, seconds=30, span=360),   # clock 5 min ahead
        _session(session_id="5" * 32, start=946684800, seconds=10),            # 2000-01-01
    ]
    for item in edges:
        assert _delivered(_put_sessions(client, acc, item), session_id=item["session_id"]), item
    assert db.query(ReadingSession).count() == len(edges)


def test_a_batch_is_all_or_nothing(db, client, acc):
    r = _put_sessions(client, acc, _session(session_id=SID), _session(session_id=SID2, seconds=1))
    assert r.status_code == 400
    assert db.query(ReadingSession).count() == 0


def test_a_batch_holds_at_most_twenty(db, client, acc):
    start = int(time.time()) - 7200
    items = [_session(session_id=f"{i:032x}", start=start) for i in range(21)]
    assert _put_sessions(client, acc, *items).status_code == 400
    assert _put_sessions(client, acc, *items[:20]).status_code == 200
    assert db.query(ReadingSession).count() == 20
    assert client.put(f"{BASE}/stats/sessions", headers=_headers(acc),
                      json={"device_id": DEVICE, "items": []}).status_code == 400


def test_an_id_reused_for_another_session_is_refused(db, client, acc):
    start = int(time.time()) - 7200
    _put_sessions(client, acc, _session(start=start))
    assert _put_sessions(client, acc, _session(start=start + 60)).status_code == 409
    assert _put_sessions(client, acc, _session(start=start, document=DOC2)).status_code == 409
    db.expire_all()
    s = db.query(ReadingSession).one()
    assert s.started_at == _utc(start)
    assert db.query(KOSyncSessionReceipt).one().document == DOC


def test_two_accounts_may_use_the_same_session_id(db, client, acc):
    """Ids are the reader's own. A reader moved to another account, or a
    restored card, must not find its queue blocked by someone else's row."""
    start = int(time.time()) - 7200
    _put_sessions(client, acc, _session(start=start, seconds=300))
    other_user = _other_user(db)
    other = _account(db, other_user)
    assert _delivered(_put_sessions(client, other, _session(start=start, seconds=3000)), session_id=SID)
    db.expire_all()
    by_user = {s.user_id: s.duration_seconds for s in db.query(ReadingSession)}
    assert by_user == {acc.user_id: 300, other_user.id: 3000}


def test_a_conflict_in_a_batch_stores_none_of_it(db, client, acc):
    start = int(time.time()) - 7200
    _put_sessions(client, acc, _session(session_id=SID, start=start))
    r = _put_sessions(client, acc, _session(session_id=SID2, document=DOC2),
                      _session(session_id=SID, start=start + 60))
    assert r.status_code == 409
    db.expire_all()
    assert db.query(ReadingSession).count() == 1 and db.query(KOSyncSessionReceipt).count() == 1


# ── the user's own changes survive a repeat ───────────────────────────────────
# The reader sends a session again as the sitting goes on, after a lost reply,
# and after an SD card is restored from a backup.

def _only_session_id(client):
    return client.get("/api/stats/sessions").json()["sessions"][0]["id"]


def test_a_trimmed_session_stays_trimmed_when_the_reader_repeats_it(db, client, acc):
    start = int(time.time()) - 20000
    _put_sessions(client, acc, _session(start=start, seconds=10000))
    sid = _only_session_id(client)
    assert client.patch(f"/api/stats/sessions/{sid}", json={"duration_seconds": 600}).status_code == 200

    assert _delivered(_put_sessions(client, acc, _session(start=start, seconds=10000)), session_id=SID)
    db.expire_all()
    s = db.query(ReadingSession).one()
    assert s.duration_seconds == 600 and s.ended_at == _utc(start + 600)


def test_a_trimmed_session_only_grows_by_new_reading(db, client, acc):
    start = int(time.time()) - 20000
    _put_sessions(client, acc, _session(start=start, seconds=10000))
    sid = _only_session_id(client)
    client.patch(f"/api/stats/sessions/{sid}", json={"duration_seconds": 600})

    _put_sessions(client, acc, _session(start=start, seconds=10300))   # 300 more seconds on the reader
    db.expire_all()
    s = db.query(ReadingSession).one()
    assert s.duration_seconds == 900 and s.ended_at == _utc(start + 10300)


def test_a_deleted_session_stays_deleted_when_the_reader_repeats_it(db, client, acc):
    start = int(time.time()) - 7200
    _put_sessions(client, acc, _session(start=start, seconds=600))
    sid = _only_session_id(client)
    assert client.delete(f"/api/stats/sessions/{sid}").status_code == 200
    db.expire_all()
    assert db.query(KOSyncSessionReceipt).one().reading_session_id is None

    # The same checkpoint again, and a later one: both acknowledged, neither stored.
    for seconds in (600, 1200):
        assert _delivered(_put_sessions(client, acc, _session(start=start, seconds=seconds)), session_id=SID)
    db.expire_all()
    assert db.query(ReadingSession).count() == 0


def test_a_restored_backup_changes_nothing(db, client, acc):
    """Every checkpoint the reader ever sent, sent again in any order."""
    start = int(time.time()) - 7200
    for seconds in (300, 900, 1800):
        _put_sessions(client, acc, _session(start=start, seconds=seconds))
    for seconds in (1800, 300, 900, 1800):
        assert _delivered(_put_sessions(client, acc, _session(start=start, seconds=seconds)), session_id=SID)
    db.expire_all()
    s = db.query(ReadingSession).one()
    assert s.duration_seconds == 1800 and s.ended_at == _utc(start + 1800)


# ── sessions without a book ───────────────────────────────────────────────────

def test_an_unmatched_session_is_not_called_a_deleted_book(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    _put_sessions(client, acc, _session())
    gone = make_book(title="Gone")
    start = datetime.utcnow() - timedelta(days=3)
    db.add(ReadingSession(user_id=user.id, book_id=gone.id, started_at=start,
                          ended_at=start + timedelta(minutes=10), duration_seconds=600, pages_turned=10))
    db.flush()
    db.delete(gone)
    db.flush()
    db.expire_all()
    titles = {row["duration_seconds"] and row["book_title"] for row in client.get("/api/stats/sessions").json()["sessions"]}
    assert titles == {"(book not linked yet)", "(deleted book)"}


def test_a_document_known_only_from_sessions_can_be_linked(db, client, admin_user, make_book):
    """The reader never synced a position for this file, only reading time."""
    user, _ = admin_user
    acc = _account(db, user, username=user.username)
    book = make_book(title="Sideloaded")
    _put_sessions(client, acc, _session())
    assert client.post(f"/api/books/{book.id}/link-kosync", json={"document": DOC2}).status_code == 404
    r = client.post(f"/api/books/{book.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(ReadingSession).one().book_id == book.id
    # ... and what the reader sends for that document from now on lands on the book.
    _put_sessions(client, acc, _session(session_id=SID2))
    db.expire_all()
    assert {s.book_id for s in db.query(ReadingSession)} == {book.id}


def test_sessions_cannot_be_linked_to_a_book_the_user_cannot_see(db, client, admin_user, make_book):
    from backend.core.security import create_access_token
    admin, _ = admin_user
    member = _other_user(db, "member1")
    acc = _account(db, member, username=member.username)
    hidden = make_book(title="In A Private Library")
    library = Library(name="Private", is_public=False, owner_id=admin.id)
    library.books.append(hidden)
    db.add(library)
    db.flush()
    _put_sessions(client, acc, _session())

    as_member = {"Authorization": f"Bearer {create_access_token(member.id)}"}
    r = client.post(f"/api/books/{hidden.id}/link-kosync", json={"document": DOC}, headers=as_member)
    assert r.status_code == 404
    db.expire_all()
    assert db.query(ReadingSession).one().book_id is None


# ── per-book snapshots ────────────────────────────────────────────────────────

def test_book_snapshots_are_acknowledged_by_count(db, client, acc):
    """v2.0.0-rc sends one book per request, development batches: the reply
    carries the real count either way."""
    h = _headers(acc)
    one = {"device_id": DEVICE, "items": [{"document": DOC, "seconds": 8400}]}
    three = {"device_id": DEVICE, "items": [{"document": f"{i:032x}"} for i in range(3)]}
    assert _delivered(client.put(f"{BASE}/stats/books", headers=h, json=one), book_count=1)
    r = client.put(f"{BASE}/stats/books", headers=h, json=three)
    assert _delivered(r, book_count=3)
    # Per-book daily seconds are not stored, so they are not claimed.
    assert r.json()["accepted_daily"] == 0
    assert db.query(ReadingSession).count() == 0


def test_book_snapshots_are_validated(client, acc):
    h = _headers(acc)
    assert client.put(f"{BASE}/stats/books", headers=h,
                      json={"device_id": DEVICE, "items": []}).status_code == 400
    assert client.put(f"{BASE}/stats/books", headers=h,
                      json={"device_id": DEVICE, "items": [{"document": "nope"}]}).status_code == 400


# ── pace ──────────────────────────────────────────────────────────────────────

def test_time_without_page_counts_does_not_slow_the_pace(db, client, admin_user, acc, make_book):
    """An uploaded session has seconds and no pages. Mixed into pages per
    minute it would read as slow reading."""
    user, _ = admin_user
    book = make_book(title="Two Devices")
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="raw"))
    start = datetime.utcnow() - timedelta(days=2)
    db.add(ReadingSession(
        user_id=user.id, book_id=book.id, started_at=start,
        ended_at=start + timedelta(hours=1), duration_seconds=3600, pages_turned=60,
    ))
    db.flush()
    _put_sessions(client, acc, _session(seconds=3600))

    own = client.get(f"/api/books/{book.id}/reading-stats").json()["own"]
    assert own["total_seconds"] == 7200
    assert own["pace_pages_per_min"] == pytest.approx(1.0)


# ── account export ────────────────────────────────────────────────────────────

def test_the_account_export_includes_uploaded_stats(client, acc):
    client.put(f"{BASE}/stats/global", headers=_headers(acc),
               json=_global(daily=[{"date": "2026-11-09", "seconds": 61}]))
    export = client.get("/api/auth/me/backup").json()["kosync_legacy"]
    assert export["device_stats"][0]["device_id"] == DEVICE
    assert export["daily_stats"] == [{"device_id": DEVICE, "day": "2026-11-09", "seconds": 61}]
