"""Per-source reading reconciliation + the explicit finish date.

Page-stats replace only *device-origin* sessions (they describe the same
reading twice); web-reader and manual-log sessions are invisible to KOReader's
history and must stay additive. Regression: logging 30 minutes of paper
reading on a Kindle-synced book returned 201 and changed nothing visible.

finished_at: updated_at moves on every rating/review/CFI write, so it is not a
finish date. The explicit column is stamped on the transition into "read".
"""
from datetime import datetime, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services.book_progress import apply_progress_to_status

DAY = 86_400
BASE = 1_700_000_000


def _pages(db, user, book, n=5, total=100, base=BASE, dur=600):
    for p in range(1, n + 1):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=total,
                        start_time=base + p, duration_seconds=dur, device="Kindle"))


def test_manual_session_visible_on_device_synced_book(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Kindle + Paper")
    _pages(db, user, book, n=5, dur=600)          # 3000s of device reading
    db.flush()

    before = client.get(f"/api/books/{book.id}/reading-stats?tz_offset=0").json()["own"]
    assert before["total_seconds"] == 3000

    r = client.post(f"/api/books/{book.id}/sessions?tz_offset=0",
                    json={"duration_minutes": 30})
    assert r.status_code == 201, r.text
    own = r.json()["own"]

    assert own["total_seconds"] == 3000 + 1800     # the paper session is not swallowed
    assert own["sessions"] == before["sessions"] + 1
    # "Where you read" now shows the split (device + manual = 2 sources).
    devices = {s["device"] for s in own["by_source"]}
    assert "Kindle" in devices and "manual" in devices


def test_web_sessions_additive_in_dashboard_totals(client, db, admin_user, make_book):
    """A covered book's web session counts in /stats; its device session doesn't
    (page-stats already describe that reading)."""
    user, _ = admin_user
    book = make_book(title="Covered Mixed")
    _pages(db, user, book, n=5, dur=600)          # 3000s page-stats
    when = datetime.utcfromtimestamp(BASE + 5 * DAY)
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when, ended_at=when,
                          duration_seconds=900, device="web"))          # additive
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when, ended_at=when,
                          duration_seconds=1200, device="Kindle"))      # double-counts → dropped
    db.flush()

    headline = client.get("/api/stats?days=0&tz_offset=0").json()["headline"]
    assert headline["total_reading_seconds"] == 3000 + 900


def test_finished_at_survives_rating_and_cfi_updates(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Finished In January")

    client.put(f"/api/books/{book.id}/status", json={"status": "read"})
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    finished = row.finished_at
    assert finished is not None

    # Rating later must not move the finish date (updated_at will move; that's fine).
    client.put(f"/api/books/{book.id}/rating", json={"rating": 5})
    db.expire_all()
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.finished_at == finished

    own = client.get(f"/api/books/{book.id}/reading-stats?tz_offset=0").json()["own"]
    assert own["finished_at"].startswith(finished.isoformat()[:19])

    # Un-finishing clears the date.
    client.put(f"/api/books/{book.id}/status", json={"status": "reading"})
    db.expire_all()
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.finished_at is None


def test_progress_helper_finishes_straight_from_unread(db, admin_user, make_book):
    """The old tome_sync if/elif quirk: an unread book synced straight to 100%
    stayed 'reading' until the next sync. The shared rule finishes it at once."""
    user, _ = admin_user
    book = make_book(title="One Sitting")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="unread"))
    db.flush()

    row = apply_progress_to_status(db, user_id=user.id, book_id=book.id, pct=1.0)
    assert row.status == "read"
    assert row.progress_pct == 1.0
    assert row.finished_at is not None


def test_manual_session_started_at_converts_timezone(client, db, admin_user, make_book):
    """'23:30+02:00' is 21:30 UTC — stripping the offset stored it as 23:30."""
    user, _ = admin_user
    book = make_book(title="TZ Aware")
    r = client.post(f"/api/books/{book.id}/sessions?tz_offset=0", json={
        "duration_minutes": 10,
        "started_at": "2026-06-01T23:30:00+02:00",
    })
    assert r.status_code == 201, r.text
    s = db.query(ReadingSession).filter_by(user_id=user.id, book_id=book.id).one()
    assert s.started_at == datetime(2026, 6, 1, 21, 30)


def test_manual_session_input_validation(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bad Inputs")
    # A huge duration used to overflow timedelta into an unhandled 500.
    r = client.post(f"/api/books/{book.id}/sessions?tz_offset=0",
                    json={"duration_minutes": 1e10})
    assert r.status_code == 422
    r = client.post(f"/api/books/{book.id}/sessions?tz_offset=0",
                    json={"duration_minutes": 10, "pages": -5})
    assert r.status_code == 422
