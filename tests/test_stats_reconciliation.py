"""2.4 — stats endpoint reconciles imported KOReader page-stats with live sessions.

Book-level rule: a book with any page-stats uses page-stats (page-stats win, its live
sessions are ignored to avoid double-counting); books with no page-stats fall back to
sessions. When no page-stats exist, behaviour is identical to before (covered elsewhere).
"""
from datetime import datetime, timezone

from backend.models.tome_sync import ReadingSession
from backend.models.ko_stats import PageStat


def _epoch(y, mo, d, h=12):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def _add_session(db, user, book, secs, when, pages=5):
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=when,
                          ended_at=when, duration_seconds=secs, pages_turned=pages))


def _add_pagestats(db, user, book, rows, day=(2026, 1, 10), device="Kindle"):
    base = _epoch(*day)
    for i, secs in enumerate(rows):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=i + 1, total_pages=100,
                        start_time=base + i * 60, duration_seconds=secs, device=device))


def test_no_double_count_for_covered_book(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Both Sources")
    _add_session(db, user, book, 100, datetime(2026, 1, 10, 12, tzinfo=timezone.utc).replace(tzinfo=None))
    _add_pagestats(db, user, book, [120, 80])   # 200s of page-stats for the same book
    db.flush()
    h = client.get("/api/stats?days=0").json()["headline"]
    # page-stats win; the 100s session is NOT added on top.
    assert h["total_reading_seconds"] == 200
    assert h["pages_turned"] == 2                # 2 page-stat rows


def test_web_only_book_falls_back_to_sessions(client, db, admin_user, make_book):
    user, _ = admin_user
    covered = make_book(title="Kindle Book")
    webonly = make_book(title="Web Book")
    _add_pagestats(db, user, covered, [200])
    _add_session(db, user, webonly, 50, datetime(2026, 1, 11, 9), pages=7)
    db.flush()
    stats = client.get("/api/stats?days=0").json()
    h = stats["headline"]
    assert h["total_reading_seconds"] == 250        # 200 page-stats + 50 session
    titles = {b["title"]: b["seconds"] for b in stats["top_books"]}
    assert titles.get("Kindle Book") == 200 and titles.get("Web Book") == 50


def test_pagestat_only_history_appears(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Old History")
    # reading recorded only in page-stats, months before any session existed
    _add_pagestats(db, user, book, [300, 300], day=(2025, 10, 20))
    db.flush()
    stats = client.get("/api/stats?days=0").json()
    assert stats["headline"]["total_reading_seconds"] == 600
    assert any(d["date"] == "2025-10-20" and d["seconds"] == 600 for d in stats["heatmap_daily"])


def test_top_books_reconciled_ordering(client, db, admin_user, make_book):
    user, _ = admin_user
    big = make_book(title="Big")
    small = make_book(title="Small")
    _add_pagestats(db, user, big, [500, 500])      # 1000s
    _add_pagestats(db, user, small, [100], day=(2026, 1, 12))
    db.flush()
    top = client.get("/api/stats?days=0").json()["top_books"]
    assert [b["title"] for b in top[:2]] == ["Big", "Small"]
