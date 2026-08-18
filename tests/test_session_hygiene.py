"""Runaway-session hygiene (issue #150).

Covers the shared detection rules (backend/services/session_hygiene.py), the
suspect-session notification raised at sync ingest, the sessions list's
book filter / longest-first sort / suspect + suggestion fields, and the
trim endpoint.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import hash_password, create_access_token
from backend.models.audit_log import AuditLog
from backend.models.notification import Notification
from backend.models.tome_sync import ApiKey, ReadingSession
from backend.models.user import User, UserPermission
from backend.services.session_hygiene import (
    SUSPECT_ABS_SECONDS,
    is_suspect,
    median_secs_per_page,
    suggested_seconds,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(db: Session, username: str) -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        role="member",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(UserPermission(user_id=user.id, can_use_kosync=True))
    db.flush()
    return user, create_access_token(subject=user.id)


def _create_api_key(db: Session, user: User) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(
        user_id=user.id,
        key_hash=ApiKey.hash_key(plaintext),
        key_prefix=plaintext[:11],
        label="test",
    ))
    db.flush()
    return plaintext


def _add_session(db, user, book, *, start, seconds, pages):
    row = ReadingSession(
        user_id=user.id,
        book_id=book.id,
        started_at=start,
        ended_at=start + timedelta(seconds=seconds),
        duration_seconds=seconds,
        pages_turned=pages,
        device="kindle",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def hygiene_client(db: Session):
    """(client, db, user, jwt, api_key) — no default auth header."""
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    user, jwt = _make_user(db, "hygieneuser")
    api_key = _create_api_key(db, user)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, db, user, jwt, api_key
    app.dependency_overrides.clear()


# ── detection rules ───────────────────────────────────────────────────────────

def test_is_suspect_rules():
    assert is_suspect(SUSPECT_ABS_SECONDS, 500)          # long outright
    assert is_suspect(3600, 4)                           # 15 min per page turn
    assert not is_suspect(3600, 60)                      # normal pace
    assert not is_suspect(None, 60)
    assert not is_suspect(0, 0)
    # long-ish but below the absolute bar, and no page data to judge pace by
    assert not is_suspect(3 * 3600, None)


def test_suggested_seconds():
    # 15 page turns at a 60s median → 900s, far below the recorded 8h
    assert suggested_seconds(8 * 3600, 15, 60.0) == 900
    # never suggests >= the recorded duration
    assert suggested_seconds(900, 15, 60.0) is None
    assert suggested_seconds(8 * 3600, None, 60.0) is None
    assert suggested_seconds(8 * 3600, 15, None) is None
    # floor of one minute
    assert suggested_seconds(8 * 3600, 1, 6.0) == 60


def test_median_ignores_the_runaways_and_needs_history(db, make_book):
    user, _ = _make_user(db, "medianuser")
    book = make_book(title="Median Book")
    start = datetime(2026, 1, 1, 20, 0)
    # two plausible sessions — not enough history yet
    _add_session(db, user, book, start=start, seconds=600, pages=10)
    _add_session(db, user, book, start=start + timedelta(days=1), seconds=1200, pages=20)
    assert median_secs_per_page(db, user.id) is None
    # third plausible one unlocks it; a runaway (1000s/page) must not skew it
    _add_session(db, user, book, start=start + timedelta(days=2), seconds=2400, pages=30)
    _add_session(db, user, book, start=start + timedelta(days=3), seconds=8 * 3600, pages=28)
    med = median_secs_per_page(db, user.id)
    assert med == pytest.approx(60.0, abs=15)


# ── ingest notification ───────────────────────────────────────────────────────

def test_suspect_sync_raises_one_notification(hygiene_client, make_book):
    c, db, user, _jwt, api_key = hygiene_client
    book = make_book(title="Night Owl")
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "book_id": book.id,
        "started_at": "2026-01-01T22:00:00Z",
        "ended_at": "2026-01-02T06:15:00Z",
        "duration_seconds": int(8.25 * 3600),
        "pages_turned": 15,
        "device": "pocketbook",
    }
    r = c.post("/api/tome-sync/session", json=payload, headers=headers)
    assert r.status_code == 201

    notes = db.query(Notification).filter_by(user_id=user.id, kind="session_suspect").all()
    assert len(notes) == 1
    assert "Night Owl" in notes[0].title
    assert notes[0].link == "/stats"

    # a second runaway for the same book while the first is unread: no stacking
    payload["started_at"] = "2026-01-03T22:00:00Z"
    payload["ended_at"] = "2026-01-04T06:15:00Z"
    payload["session_uuid"] = "other-night"
    r = c.post("/api/tome-sync/session", json=payload, headers=headers)
    assert r.status_code == 201
    notes = db.query(Notification).filter_by(user_id=user.id, kind="session_suspect").all()
    assert len(notes) == 1


def test_normal_sync_raises_no_notification(hygiene_client, make_book):
    c, db, user, _jwt, api_key = hygiene_client
    book = make_book(title="Normal Evening")
    r = c.post("/api/tome-sync/session", json={
        "book_id": book.id,
        "started_at": "2026-01-01T20:00:00Z",
        "ended_at": "2026-01-01T21:00:00Z",
        "duration_seconds": 3600,
        "pages_turned": 60,
        "device": "kindle",
    }, headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 201
    assert db.query(Notification).filter_by(user_id=user.id).count() == 0


# ── sessions list: filter, sort, flags ────────────────────────────────────────

def test_sessions_list_filter_sort_and_flags(hygiene_client, make_book):
    c, db, user, jwt, _key = hygiene_client
    book_a = make_book(title="Book A")
    book_b = make_book(title="Book B")
    start = datetime(2026, 1, 1, 20, 0)
    # plausible history so the median (60s/page) and suggestions exist
    for i in range(3):
        _add_session(db, user, book_a, start=start + timedelta(days=i), seconds=1800, pages=30)
    runaway = _add_session(
        db, user, book_b, start=start + timedelta(days=10), seconds=8 * 3600, pages=15
    )
    headers = {"Authorization": f"Bearer {jwt}"}

    r = c.get(f"/api/stats/sessions?book_id={book_b.id}", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    row = data["sessions"][0]
    assert row["id"] == runaway.id
    assert row["suspect"] is True
    assert row["suggested_seconds"] == pytest.approx(15 * 60, abs=300)

    r = c.get("/api/stats/sessions?sort=longest", headers=headers)
    rows = r.json()["sessions"]
    assert rows[0]["id"] == runaway.id
    assert all(not row["suspect"] for row in rows[1:])

    r = c.get("/api/stats/sessions?sort=bogus", headers=headers)
    assert r.status_code == 422


# ── trim endpoint ─────────────────────────────────────────────────────────────

def test_trim_shortens_and_moves_ended_at(hygiene_client, make_book):
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Trim Me")
    start = datetime(2026, 1, 1, 22, 0)
    row = _add_session(db, user, book, start=start, seconds=8 * 3600, pages=15)
    headers = {"Authorization": f"Bearer {jwt}"}

    r = c.patch(f"/api/stats/sessions/{row.id}", json={"duration_seconds": 900}, headers=headers)
    assert r.status_code == 200

    db.expire_all()
    fresh = db.get(ReadingSession, row.id)
    assert fresh.duration_seconds == 900
    assert fresh.ended_at == start + timedelta(seconds=900)

    entry = db.query(AuditLog).filter_by(action="session.trimmed").first()
    assert entry is not None


def test_trim_validation(hygiene_client, make_book):
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Trim Bounds")
    row = _add_session(
        db, user, book, start=datetime(2026, 1, 1, 22, 0), seconds=600, pages=10
    )
    headers = {"Authorization": f"Bearer {jwt}"}

    # can only shorten
    r = c.patch(f"/api/stats/sessions/{row.id}", json={"duration_seconds": 600}, headers=headers)
    assert r.status_code == 400
    r = c.patch(f"/api/stats/sessions/{row.id}", json={"duration_seconds": 0}, headers=headers)
    assert r.status_code == 400
    # not someone else's session
    other, other_jwt = _make_user(db, "someoneelse")
    r = c.patch(
        f"/api/stats/sessions/{row.id}",
        json={"duration_seconds": 60},
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    assert r.status_code == 404


# ── imported KOReader history in the session list ─────────────────────────────

def _add_page_stats(db, user, book, *, base, count=5, dur=60, device="Kindle", page0=1):
    from backend.models.ko_stats import PageStat
    for i in range(count):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=page0 + i,
                        total_pages=100, start_time=base + i * dur,
                        duration_seconds=dur, device=device))
    db.flush()


def test_imported_clusters_listed_device_sessions_labeled(hygiene_client, make_book):
    """Imported sittings appear as `kind: "imported"` rows. A covered book's
    device-origin live session (the same reading, described twice) stays
    listed but is `counted: false` — labeled, never silently hidden.
    Web/manual sessions stay counted."""
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Covered Book")
    base = 1_700_000_000  # 2023-11-14
    _add_page_stats(db, user, book, base=base, count=5, dur=60)                 # sitting 1: 300s
    _add_page_stats(db, user, book, base=base + 7200, count=3, dur=120, page0=10)  # sitting 2: 360s
    # Device-origin live session recorded during sitting 1 (base = 22:13:20Z)
    # — the same reading described twice: listed, but not counted.
    superseded = _add_session(db, user, book, start=datetime(2023, 11, 14, 22, 14), seconds=600, pages=10)
    # Manual log — additive, listed and counted.
    manual = ReadingSession(user_id=user.id, book_id=book.id,
                            started_at=datetime(2026, 7, 1, 20, 0),
                            duration_seconds=900, pages_turned=12, device="manual")
    db.add(manual)
    db.flush()
    headers = {"Authorization": f"Bearer {jwt}"}

    r = c.get("/api/stats/sessions", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    by_id = {row["id"]: row for row in data["sessions"]}
    assert by_id[manual.id]["counted"] is True
    assert by_id[superseded.id]["counted"] is False
    imported = [row for row in data["sessions"] if row["kind"] == "imported"]
    assert {row["duration_seconds"] for row in imported} == {300, 360}
    for row in imported:
        assert row["counted"] is True
        assert row["range_start"] <= row["range_end"]
        assert row["device"] == "Kindle"
        assert row["suggested_seconds"] is None

    # longest-first: manual 900, superseded device 600, sittings 360 / 300
    r = c.get("/api/stats/sessions?sort=longest", headers=headers)
    assert [row["duration_seconds"] for row in r.json()["sessions"]] == [900, 600, 360, 300]

    # merged pagination: pages concatenate to the full list without dup or gap
    r1 = c.get("/api/stats/sessions?limit=2&offset=0", headers=headers).json()
    r2 = c.get("/api/stats/sessions?limit=2&offset=2", headers=headers).json()
    ids = [row["id"] for row in r1["sessions"] + r2["sessions"]]
    assert len(ids) == 4 == len(set(ids))


def test_imported_cluster_delete(hygiene_client, make_book):
    """Deleting an imported row removes exactly that sitting's page-stats;
    deleting the sitting a live session overlaps un-supersedes that session,
    so it counts again."""
    from backend.models.ko_stats import PageStat
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Accidental Open")
    base = 1_700_000_000  # 2023-11-14 22:13:20Z
    _add_page_stats(db, user, book, base=base, count=5, dur=60)                 # real sitting
    _add_page_stats(db, user, book, base=base + 7200, count=2, dur=150, page0=50)  # accidental
    # live device session recorded during the real sitting — superseded by it
    live = _add_session(db, user, book, start=datetime(2023, 11, 14, 22, 14), seconds=400, pages=7)
    live_id = live.id  # the instance detaches once the endpoint commits/rolls back
    headers = {"Authorization": f"Bearer {jwt}"}

    rows = c.get(f"/api/stats/sessions?book_id={book.id}", headers=headers).json()["sessions"]
    accidental = next(r for r in rows if r["kind"] == "imported" and r["duration_seconds"] == 300)

    r = c.delete(
        f"/api/stats/imported-sessions?book_id={book.id}"
        f"&start={accidental['range_start']}&end={accidental['range_end']}",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["deleted_rows"] == 2
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=book.id).count() == 5
    entry = db.query(AuditLog).filter_by(action="imported_session.deleted").first()
    assert entry is not None

    # same range again: nothing left there
    r = c.delete(
        f"/api/stats/imported-sessions?book_id={book.id}"
        f"&start={accidental['range_start']}&end={accidental['range_end']}",
        headers=headers,
    )
    assert r.status_code == 404

    # the real sitting remains and still describes the live session: not counted
    rows = c.get(f"/api/stats/sessions?book_id={book.id}", headers=headers).json()["sessions"]
    assert [(r["kind"], r["counted"]) for r in rows] == [("session", False), ("imported", True)]
    remaining = rows[1]

    # delete that sitting → nothing overlaps the live session → it counts again
    r = c.delete(
        f"/api/stats/imported-sessions?book_id={book.id}"
        f"&start={remaining['range_start']}&end={remaining['range_end']}",
        headers=headers,
    )
    assert r.status_code == 200
    rows = c.get(f"/api/stats/sessions?book_id={book.id}", headers=headers).json()["sessions"]
    assert [(r["id"], r["counted"]) for r in rows] == [(live_id, True)]


def test_imported_delete_scoped_to_own_rows(hygiene_client, make_book):
    """The range delete never touches another user's page-stats."""
    from backend.models.ko_stats import PageStat
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Shared Book")
    base = 1_700_000_000
    _add_page_stats(db, user, book, base=base, count=3, dur=60)
    other, other_jwt = _make_user(db, "otherreader")
    _add_page_stats(db, other, book, base=base, count=3, dur=60)

    r = c.delete(
        f"/api/stats/imported-sessions?book_id={book.id}&start={base}&end={base + 600}",
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    assert r.status_code == 200
    assert db.query(PageStat).filter_by(user_id=user.id).count() == 3
    assert db.query(PageStat).filter_by(user_id=other.id).count() == 0


def test_sessions_day_filter(hygiene_client, make_book):
    """`day` narrows the merged list to sittings that started that reading day
    (Activity-bar click-through)."""
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Day Filter Book")
    base = 1_700_000_000  # 2023-11-14 22:13 UTC → reading day 2023-11-14 (4h rollover)
    _add_page_stats(db, user, book, base=base, count=5, dur=60)
    _add_page_stats(db, user, book, base=base + 5 * 86_400, count=3, dur=120, page0=10)
    manual = ReadingSession(user_id=user.id, book_id=book.id,
                            started_at=datetime(2023, 11, 14, 23, 30),
                            duration_seconds=600, pages_turned=8, device="manual")
    db.add(manual)
    db.flush()
    headers = {"Authorization": f"Bearer {jwt}"}

    r = c.get(f"/api/stats/sessions?book_id={book.id}&day=2023-11-14&tz_offset=0", headers=headers)
    data = r.json()
    assert data["total"] == 2
    assert {row["kind"] for row in data["sessions"]} == {"session", "imported"}

    r = c.get(f"/api/stats/sessions?book_id={book.id}&day=2023-11-19&tz_offset=0", headers=headers)
    data = r.json()
    assert data["total"] == 1
    assert data["sessions"][0]["kind"] == "imported"
    assert data["sessions"][0]["duration_seconds"] == 360

    r = c.get(f"/api/stats/sessions?book_id={book.id}&day=14-11-2023", headers=headers)
    assert r.status_code == 422


def test_noise_blip_listed_and_deletable(hygiene_client, make_book):
    """A sub-10s page-stat blip (accidental open) is not a counted sitting, but
    it holds seconds in the totals — so the list shows it and it can be
    deleted. Caught on prod: an Activity bar whose day popup said "No sessions
    recorded" because the only sitting that day was 7 seconds long."""
    c, db, user, jwt, _key = hygiene_client
    book = make_book(title="Blip Book")
    base = 1_700_000_000
    _add_page_stats(db, user, book, base=base, count=5, dur=60)          # real sitting
    _add_page_stats(db, user, book, base=base + 40 * 86_400, count=1, dur=7,
                    page0=99)                                            # 7s accidental open
    headers = {"Authorization": f"Bearer {jwt}"}

    rows = c.get(f"/api/stats/sessions?book_id={book.id}&tz_offset=0",
                 headers=headers).json()["sessions"]
    assert [r["duration_seconds"] for r in rows] == [7, 300]

    # the blip's reading day resolves like the timeline's: filterable by day
    blip = rows[0]
    day = blip["started_at"][:10]
    rows = c.get(f"/api/stats/sessions?book_id={book.id}&day={day}&tz_offset=0",
                 headers=headers).json()["sessions"]
    assert [r["duration_seconds"] for r in rows] == [7]

    # per-book count keeps the noise floor: one sitting, not two
    r = c.get(f"/api/books/{book.id}/reading-stats", headers=headers)
    assert r.json()["own"]["sessions"] == 1

    # and it is deletable like any imported sitting
    r = c.delete(
        f"/api/stats/imported-sessions?book_id={book.id}"
        f"&start={blip['range_start']}&end={blip['range_end']}",
        headers=headers,
    )
    assert r.status_code == 200
    rows = c.get(f"/api/stats/sessions?book_id={book.id}&tz_offset=0",
                 headers=headers).json()["sessions"]
    assert [r["duration_seconds"] for r in rows] == [300]
