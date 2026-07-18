"""GET /stats/timeline — lifetime per-book reading lanes for the Timeline tab.

Reconciliation contract matches the rest of stats: a book with imported
page-stats uses them for device reading (its device-origin sessions are
ignored), web/manual sessions stay additive, session-only books fall back
to sessions entirely.
"""
from datetime import datetime, timezone

from backend.core.security import hash_password, create_access_token
from backend.models.tome_sync import ReadingSession
from backend.models.ko_stats import PageStat
from backend.models.library import Library
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus


def _epoch(y, mo, d, h=12):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def _add_session(db, user, book, secs, when, device=None):
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when,
                          ended_at=when, duration_seconds=secs, pages_turned=5,
                          device=device))


def _add_pagestats(db, user, book, rows, day=(2026, 1, 10)):
    base = _epoch(*day)
    for i, secs in enumerate(rows):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=i + 1, total_pages=100,
                        start_time=base + i * 60, duration_seconds=secs, device="Kindle"))


def _timeline(client):
    r = client.get("/api/stats/timeline")
    assert r.status_code == 200
    return r.json()


def test_timeline_empty(client):
    data = _timeline(client)
    assert data["books"] == []
    assert data["today"]


def test_timeline_reconciles_sources(client, db, admin_user, make_book):
    user, _ = admin_user
    covered = make_book(title="Kindle Book")
    webonly = make_book(title="Web Book")
    # covered: page-stats on two days + a device session that must NOT double-count
    _add_pagestats(db, user, covered, [120, 80], day=(2026, 1, 10))
    _add_pagestats(db, user, covered, [300], day=(2026, 1, 14))
    _add_session(db, user, covered, 999, datetime(2026, 1, 10, 12))          # device-origin: ignored
    _add_session(db, user, covered, 60, datetime(2026, 1, 12, 21), device="web")  # additive
    # session-only book
    _add_session(db, user, webonly, 90, datetime(2026, 2, 1, 9), device="web")
    db.flush()

    books = {b["title"]: b for b in _timeline(client)["books"]}
    kb = books["Kindle Book"]
    assert kb["first_day"] == "2026-01-10"
    assert kb["last_day"] == "2026-01-14"
    assert kb["total_seconds"] == 120 + 80 + 300 + 60          # 999 dropped
    assert [d["date"] for d in kb["days"]] == ["2026-01-10", "2026-01-12", "2026-01-14"]
    assert {d["date"]: d["seconds"] for d in kb["days"]}["2026-01-12"] == 60

    wb = books["Web Book"]
    assert wb["first_day"] == wb["last_day"] == "2026-02-01"
    assert wb["total_seconds"] == 90


def test_timeline_ordered_and_meta(client, db, admin_user, make_book):
    user, _ = admin_user
    late = make_book(title="Later Book")
    early = make_book(title="Earlier Book")
    _add_session(db, user, late, 400, datetime(2026, 3, 5, 9), device="web")
    _add_session(db, user, early, 400, datetime(2025, 11, 2, 9), device="web")
    db.add(UserBookStatus(user_id=user.id, book_id=late.id, status="read",
                          finished_at=datetime(2026, 3, 6, 8)))
    db.flush()

    data = _timeline(client)
    titles = [b["title"] for b in data["books"]]
    assert titles == ["Earlier Book", "Later Book"]            # sorted by first activity
    lb = data["books"][1]
    assert lb["finished_on"] == "2026-03-06"


def test_timeline_drops_subminute_noise(client, db, admin_user, make_book):
    user, _ = admin_user
    noise = make_book(title="Page Flip")
    real = make_book(title="Real Read")
    _add_session(db, user, noise, 12, datetime(2026, 4, 1, 9), device="web")
    _add_session(db, user, real, 300, datetime(2026, 4, 1, 9), device="web")
    db.flush()
    titles = [b["title"] for b in _timeline(client)["books"]]
    assert titles == ["Real Read"]


def test_timeline_excludes_soft_deleted(client, db, admin_user, make_book):
    user, _ = admin_user
    gone = make_book(title="Soft Deleted")
    _add_session(db, user, gone, 300, datetime(2026, 5, 1, 9), device="web")
    gone.status = "deleted"
    db.flush()
    assert "Soft Deleted" not in [b["title"] for b in _timeline(client)["books"]]


def test_timeline_respects_visibility(client, db, admin_user, make_book):
    """A member's reading on a book that later moved into someone else's private
    library disappears from their timeline — same rule as every other stats
    surface (book_visibility_filter is the single source of truth)."""
    admin, _ = admin_user
    hidden = make_book(title="Private Book")
    lib = Library(name="Admin Private", is_public=False, owner_id=admin.id)
    db.add(lib)
    db.flush()
    hidden.libraries.append(lib)

    member = User(username="tl_member", email="tl_member@example.com",
                  hashed_password=hash_password("pass1234"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    _add_session(db, member, hidden, 600, datetime(2026, 5, 2, 9), device="web")
    db.flush()

    token = create_access_token(subject=member.id)
    r = client.get("/api/stats/timeline",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["books"] == []
