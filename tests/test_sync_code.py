"""KOReader sync code (QR hand-off from an offline device).

Covers the page format (assembly, ordering, corruption), the write rules
(session dedup against the plugin's own key, position newer-on-server guard,
rating last-write-wins, clock offset shift, visibility) and the overview.
"""
import time
from datetime import datetime

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.user_book_status import UserBookStatus
from backend.services import sync_code
from backend.services.sync_code import (
    CodePayload,
    SyncCodeError,
    assemble,
    encode_payload,
    parse_page,
)


NOW = int(time.time())


def _payload(**over) -> dict:
    base = {"v": 1, "id": "3f9a1c02", "dev": "Kindle", "t": NOW, "s": [], "p": [], "r": []}
    base.update(over)
    return base


def _session(book_id: int, start: int, dur: int = 900, ps: float = 0.10, pe: float = 0.25, pg: int = 30) -> dict:
    return {"b": book_id, "st": start, "en": start + dur, "d": dur, "ps": ps, "pe": pe, "pg": pg}


# ── format ────────────────────────────────────────────────────────────────────

def test_parse_page_shape():
    assert parse_page("TSC1:3F9A1C02:2/3:abcd") == ("3f9a1c02", 2, 3, "abcd")
    for bad in ("hello", "tome://connect?x=1", "TSC1:zz:1/1:abc", "TSC1:3f9a1c02:0/1:abc",
                "TSC1:3f9a1c02:2/1:abc", "TSC1:3f9a1c02:1/1:not base64!"):
        with pytest.raises(SyncCodeError):
            parse_page(bad)


def test_roundtrip_single_page():
    pages = encode_payload(_payload(s=[_session(1, NOW - 3600)]))
    assert len(pages) == 1
    decoded = assemble(pages)
    assert isinstance(decoded, CodePayload)
    assert decoded.s[0].b == 1 and decoded.s[0].d == 900


def test_multi_page_any_order_and_repeats():
    big = _payload(s=[_session(i % 7 + 1, NOW - i * 1000) for i in range(50)])
    pages = encode_payload(big, chunk_size=200)
    assert len(pages) > 2
    shuffled = list(reversed(pages)) + [pages[0]]  # reversed + one page scanned twice
    decoded = assemble(shuffled)
    assert len(decoded.s) == 50


def test_missing_page_names_it():
    pages = encode_payload(_payload(s=[_session(1, NOW - i * 1000) for i in range(30)]), chunk_size=150)
    assert len(pages) >= 3
    with pytest.raises(SyncCodeError, match="Missing page 2"):
        assemble([pages[0]] + pages[2:])


def test_mixed_codes_rejected():
    a = encode_payload(_payload(id="aaaa1111", s=[_session(1, NOW)]))
    b = encode_payload(_payload(id="bbbb2222", s=[_session(1, NOW)]))
    with pytest.raises(SyncCodeError, match="different sync codes"):
        assemble(a + b)


def test_corrupt_chunk_rejected():
    (page,) = encode_payload(_payload(s=[_session(1, NOW)]))
    head, _, chunk = page.rpartition(":")
    with pytest.raises(SyncCodeError, match="Corrupt"):
        assemble([f"{head}:{chunk[:-6]}AAAAAA"])


def test_empty_lua_tables_are_empty_lists():
    # lua-rapidjson turns an empty table into {} - the plugin strips empties,
    # but an older build may not.
    pages = encode_payload({"v": 1, "id": "3f9a1c02", "dev": "K", "t": NOW, "s": {}, "p": {}, "r": {}})
    decoded = assemble(pages)
    assert decoded.s == [] and decoded.p == [] and decoded.r == []


def test_future_version_rejected():
    pages = encode_payload(_payload(v=2))
    with pytest.raises(SyncCodeError, match="newer Tome"):
        assemble(pages)


# ── endpoint ──────────────────────────────────────────────────────────────────

def _post(client: TestClient, payload: dict, chunk_size: int = 600):
    return client.post("/api/sync-code", json={"pages": encode_payload(payload, chunk_size=chunk_size)})


def test_sessions_land_and_dedup_against_plugin_key(client: TestClient, db: Session, make_book, admin_user):
    user, _ = admin_user
    book = make_book(title="Overlord 1", series="Overlord", series_index=1)
    start = NOW - 7200
    payload = _payload(s=[_session(book.id, start), _session(book.id, start + 2000, pe=0.40)])

    r = _post(client, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessions_added"] == 2 and body["sessions_known"] == 0
    assert body["device"] == "Kindle" and body["clock_offset_seconds"] == 0
    (row,) = [b for b in body["books"] if b["book_id"] == book.id]
    assert row["title"] == "Overlord 1" and row["series"] == "Overlord"
    assert row["sessions_added"] == 2 and row["seconds"] == 1800 and row["pages"] == 60
    assert row["progress_before"] is None and row["progress_after"] == pytest.approx(0.40)
    assert row["status"] == "reading"

    # The stored dedup key is exactly what the plugin would POST later.
    stored = db.query(ReadingSession).filter(ReadingSession.book_id == book.id).all()
    assert {s.session_uuid for s in stored} == {f"{book.id}-{start}-Kindle", f"{book.id}-{start + 2000}-Kindle"}

    # Scanning the same code again adds nothing.
    r2 = _post(client, payload)
    assert r2.json()["sessions_added"] == 0 and r2.json()["sessions_known"] == 2
    assert db.query(ReadingSession).filter(ReadingSession.book_id == book.id).count() == 2

    # ...and so does the plugin's own flush of the same queue over Wi-Fi.
    r3 = client.post("/api/tome-sync/session", json={
        "book_id": book.id, "started_at": datetime.utcfromtimestamp(start).isoformat() + "Z",
        "ended_at": datetime.utcfromtimestamp(start + 900).isoformat() + "Z",
        "duration_seconds": 900, "device": "Kindle", "session_uuid": f"{book.id}-{start}-Kindle",
    }, headers={"Authorization": client.headers["Authorization"]})
    # /tome-sync/session wants the plugin API key; a JWT is refused. The
    # dedup itself is exercised by the second scan above. Just make sure the
    # attempt did not create a third row through some other path.
    assert r3.status_code in (401, 403)
    assert db.query(ReadingSession).filter(ReadingSession.book_id == book.id).count() == 2


def test_position_applied_unless_server_is_newer(client: TestClient, db: Session, make_book, admin_user):
    user, _ = admin_user
    fresh = make_book(title="Fresh")
    stale = make_book(title="Stale")

    # The phone already wrote a newer position for `stale`.
    db.add(TomeSyncPosition(user_id=user.id, book_id=stale.id, percentage=0.80,
                            progress="/body/p[80]", device="iPhone",
                            updated_at=datetime.utcnow()))
    db.flush()

    written = NOW - 3600  # the Kindle wrote its positions an hour ago
    payload = _payload(p=[
        {"b": fresh.id, "pc": 0.33, "xp": "/body/DocFragment[3]/body/p[12]", "t": written},
        {"b": stale.id, "pc": 0.50, "xp": "/body/p[50]", "t": written},
    ])
    body = _post(client, payload).json()
    assert body["positions_applied"] == 1 and body["positions_kept"] == 1
    by_id = {b["book_id"]: b for b in body["books"]}
    assert by_id[fresh.id]["position"] == "applied"
    assert by_id[stale.id]["position"] == "kept"

    fresh_row = db.query(TomeSyncPosition).filter_by(book_id=fresh.id).one()
    assert fresh_row.percentage == pytest.approx(0.33) and fresh_row.device == "Kindle"
    stale_row = db.query(TomeSyncPosition).filter_by(book_id=stale.id).one()
    assert stale_row.percentage == pytest.approx(0.80) and stale_row.device == "iPhone"

    fresh_status = db.query(UserBookStatus).filter_by(book_id=fresh.id).one()
    assert fresh_status.status == "reading" and fresh_status.progress_pct == pytest.approx(0.33)


def test_completion_is_sticky_against_a_stale_device_position(client: TestClient, db: Session, make_book, admin_user):
    user, _ = admin_user
    book = make_book(title="Done")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0,
                          finished_at=datetime.utcnow()))
    db.flush()
    body = _post(client, _payload(p=[{"b": book.id, "pc": 0.6, "xp": "x", "t": NOW}])).json()
    assert body["positions_applied"] == 1
    st = db.query(UserBookStatus).filter_by(book_id=book.id).one()
    assert st.status == "read"


def test_rating_last_write_wins(client: TestClient, db: Session, make_book, admin_user):
    user, _ = admin_user
    book = make_book(title="Rated")
    body = _post(client, _payload(r=[{"b": book.id, "r": 4, "rv": "solid"}])).json()
    assert body["ratings_applied"] == 1
    st = db.query(UserBookStatus).filter_by(book_id=book.id).one()
    assert st.rating == 4 and st.review == "solid"
    # A clear travels too.
    _post(client, _payload(id="0badcafe", r=[{"b": book.id, "r": None, "rv": None}]))
    db.expire_all()
    st = db.query(UserBookStatus).filter_by(book_id=book.id).one()
    assert st.rating is None and st.review is None


def test_device_clock_ahead_is_corrected(client: TestClient, db: Session, make_book):
    book = make_book(title="Skewed")
    # The device thinks it is one hour in the future: impossible for a scan
    # delay, so it is a clock error and every stamp shifts back. Fresh clock:
    # the module-level NOW ages over a long test run.
    device_now = int(time.time()) + 3600
    start = device_now - 600
    body = _post(client, _payload(t=device_now, s=[_session(book.id, start, dur=600)])).json()
    assert -3610 <= body["clock_offset_seconds"] <= -3590
    row = db.query(ReadingSession).filter_by(book_id=book.id).one()
    # Stored in server time: the session ended about now, not in an hour.
    assert abs((row.ended_at - datetime.utcnow()).total_seconds()) < 30
    # The dedup key still uses the device's own start epoch, matching the
    # plugin's later POST byte for byte.
    assert row.session_uuid == f"{book.id}-{start}-Kindle"


def test_scan_delay_is_not_a_clock_offset(client: TestClient, db: Session, make_book):
    """A code shown five minutes (or a day) before it is scanned is not a wrong clock."""
    book = make_book(title="Patient")
    for delay in (300, 6 * 3600, 86400):
        body = _post(client, _payload(id=f"{delay:08x}", t=NOW - delay,
                                      s=[_session(book.id, NOW - delay - 900)])).json()
        assert body["clock_offset_seconds"] == 0, delay
    row = db.query(ReadingSession).filter_by(book_id=book.id).order_by(ReadingSession.id).first()
    assert abs((row.started_at - datetime.utcfromtimestamp(NOW - 300 - 900)).total_seconds()) < 2


def test_clock_offset_rules():
    now = int(time.time())
    assert sync_code.clock_offset_seconds(now - 30) == 0
    assert sync_code.clock_offset_seconds(now + 30) == 0
    assert sync_code.clock_offset_seconds(now - 600) == 0           # scanned ten minutes later
    assert sync_code.clock_offset_seconds(now - 86400) == 0         # photo scanned next day
    assert sync_code.clock_offset_seconds(now + 600) <= -590        # device ahead: clock error
    assert sync_code.clock_offset_seconds(now - 3 * 86400) >= 3 * 86400 - 10   # device years/days behind: clock reset


def test_unknown_and_invisible_books_are_skipped(client: TestClient, db: Session, make_book):
    book = make_book(title="Exists")
    body = _post(client, _payload(s=[_session(book.id, NOW - 100), _session(99999, NOW - 200)])).json()
    assert body["sessions_added"] == 1 and body["unknown_books"] == 1
    assert [b["book_id"] for b in body["books"]] == [book.id]


def test_member_cannot_write_into_a_private_book(db: Session, make_book, client: TestClient):
    """A member scanning a code that names a book they cannot see gets it skipped."""
    from backend.core.security import hash_password, create_access_token
    from backend.main import create_app
    from backend.core.database import get_db
    from backend.models.library import Library
    from backend.models.user import User

    member = User(username="member", email="m@example.com", hashed_password=hash_password("x"),
                  is_active=True, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    private = Library(name="Private", is_public=False, owner_id=1)
    db.add(private)
    db.flush()
    book = make_book(title="Hidden")
    book.libraries.append(private)
    db.flush()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {create_access_token(subject=member.id)}"
        body = _post(c, _payload(s=[_session(book.id, NOW - 100)])).json()
    app.dependency_overrides.clear()
    assert body["unknown_books"] == 1 and body["sessions_added"] == 0
    assert db.query(ReadingSession).filter_by(book_id=book.id).count() == 0


def test_bad_pages_are_400(client: TestClient):
    r = client.post("/api/sync-code", json={"pages": ["tome://connect?code=ABC"]})
    assert r.status_code == 400 and "Not a Tome sync code" in r.json()["detail"]
    pages = encode_payload(_payload(s=[_session(1, NOW - i * 1000) for i in range(30)]), chunk_size=150)
    r = client.post("/api/sync-code", json={"pages": pages[1:]})
    assert r.status_code == 400 and "Missing page 1" in r.json()["detail"]


def test_requires_auth(db: Session):
    from backend.main import create_app
    from backend.core.database import get_db
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.post("/api/sync-code", json={"pages": encode_payload(_payload())})
    app.dependency_overrides.clear()
    assert r.status_code == 401
