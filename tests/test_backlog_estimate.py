"""Backlog completion estimates (#187): per-book, per-series and scoped."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.library import BookType, Library, SavedFilter
from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services import backlog_estimate as be


def _session(db: Session, user_id: int, book_id: int, seconds: int, days_ago: int = 1) -> None:
    started = datetime.utcnow() - timedelta(days=days_ago)
    db.add(ReadingSession(
        user_id=user_id, book_id=book_id, started_at=started,
        ended_at=started + timedelta(seconds=seconds),
        duration_seconds=seconds, pages_turned=10,
    ))
    db.flush()


def _book(db: Session, make_book, title: str, *, word_count: int | None = None,
          book_type_id: int | None = None, **kw):
    """make_book doesn't take word_count/book_type_id — set them after."""
    b = make_book(title=title, **kw)
    b.word_count = word_count
    b.book_type_id = book_type_id
    db.flush()
    return b


def _status(db: Session, user_id: int, book_id: int, status: str) -> None:
    db.add(UserBookStatus(user_id=user_id, book_id=book_id, status=status))
    db.flush()


def _finish(db: Session, user_id: int, book_id: int, seconds: int, days_ago: int = 1) -> None:
    _session(db, user_id, book_id, seconds, days_ago)
    _status(db, user_id, book_id, "read")


# ── service ───────────────────────────────────────────────────────────────────

def test_no_history_uses_default_wpm(db, admin_user, make_book):
    user, _ = admin_user
    book = _book(db, make_book, "Fresh", word_count=25_000)
    pace = be.compute_pace(db, user.id)
    assert pace.wpm is None and pace.minutes_per_day is None
    est = be.estimate_book(book, pace)
    assert est["method"] == "default"
    assert est["seconds"] == round(25_000 / be.DEFAULT_WPM * 60)
    assert est["days"] is None  # no recent reading → no day forecast


def test_measured_wpm_and_days(db, admin_user, make_book):
    user, _ = admin_user
    done = _book(db, make_book, "Done", word_count=60_000)
    _finish(db, user.id, done.id, seconds=3600, days_ago=2)  # 1000 wpm, 1 h in the last 30 days
    target = _book(db, make_book, "Next", word_count=30_000)

    pace = be.compute_pace(db, user.id)
    assert pace.wpm == 1000.0
    assert pace.window_days == 30
    assert pace.minutes_per_day == 2.0  # 60 min / 30 days

    est = be.estimate_book(target, pace)
    assert est["method"] == "words"
    assert est["seconds"] == 1800
    assert est["days"] == 15.0


def test_short_finish_ignored_for_wpm(db, admin_user, make_book):
    """Sub-5-minute 'finishes' (a mis-tap on Mark read) don't poison the pace."""
    user, _ = admin_user
    done = _book(db, make_book, "Tap", word_count=90_000)
    _finish(db, user.id, done.id, seconds=120)
    assert be.compute_pace(db, user.id).wpm is None


def test_type_average_fallback_needs_two_finished(db, admin_user, make_book):
    user, _ = admin_user
    manga = BookType(slug="manga-t", label="Manga")
    db.add(manga); db.flush()
    v1 = _book(db, make_book, "V1", book_type_id=manga.id)
    v2 = _book(db, make_book, "V2", book_type_id=manga.id)
    target = _book(db, make_book, "V3", book_type_id=manga.id)  # no word count

    _finish(db, user.id, v1.id, seconds=1200)
    pace = be.compute_pace(db, user.id)
    assert be.estimate_book(target, pace)["method"] is None  # one finished volume isn't a sample

    _finish(db, user.id, v2.id, seconds=1800)
    pace = be.compute_pace(db, user.id)
    est = be.estimate_book(target, pace)
    assert est["method"] == "type_avg"
    assert est["seconds"] == 1500


def test_ninety_day_fallback_window(db, admin_user, make_book):
    user, _ = admin_user
    done = _book(db, make_book, "Old", word_count=10_000)
    _finish(db, user.id, done.id, seconds=9000, days_ago=45)  # outside 30d, inside 90d
    pace = be.compute_pace(db, user.id)
    assert pace.window_days == 90
    assert pace.minutes_per_day == round(150 / 90, 1)


def test_summarise_by_type_and_unestimated(db, admin_user, make_book):
    user, _ = admin_user
    novels = BookType(slug="novels-t", label="Novels")
    comics = BookType(slug="comics-t", label="Comics")
    db.add_all([novels, comics]); db.flush()
    books = [
        _book(db, make_book, "A", word_count=25_000, book_type_id=novels.id),
        _book(db, make_book, "B", word_count=50_000, book_type_id=novels.id),
        _book(db, make_book, "C", book_type_id=comics.id),  # nothing to go on
    ]
    s = be.summarise(db, books, be.compute_pace(db, user.id))
    assert (s["books"], s["estimated"], s["unestimated"]) == (3, 2, 1)
    assert s["seconds"] == round(75_000 / be.DEFAULT_WPM * 60)
    assert [r["label"] for r in s["by_type"]] == ["Novels", "Comics"]
    assert s["by_type"][1]["unestimated"] == 1 and s["by_type"][1]["seconds"] == 0


# ── endpoints ─────────────────────────────────────────────────────────────────

def test_book_estimate_endpoint(client: TestClient, db, make_book):
    book = _book(db, make_book, "E", word_count=12_500)
    r = client.get(f"/api/books/{book.id}/estimate")
    assert r.status_code == 200
    assert r.json()["seconds"] == 3000 and r.json()["method"] == "default"
    assert client.get("/api/books/999999/estimate").status_code == 404


def test_series_detail_backlog_counts_unstarted_only(client: TestClient, db, admin_user, make_book):
    user, _ = admin_user
    v1 = _book(db, make_book, "S1", series="Saga", series_index=1, word_count=10_000)
    v2 = _book(db, make_book, "S2", series="Saga", series_index=2, word_count=10_000)
    v3 = _book(db, make_book, "S3", series="Saga", series_index=3, word_count=10_000)
    _status(db, user.id, v1.id, "read")
    _status(db, user.id, v2.id, "reading")
    r = client.get("/api/books/series-detail", params={"name": "Saga"})
    assert r.status_code == 200
    backlog = r.json()["backlog"]
    assert backlog["books"] == 1 and backlog["seconds"] == round(10_000 / 250 * 60)
    del v3


def test_stats_backlog_scopes(client: TestClient, db, admin_user, make_book):
    user, _ = admin_user
    lib = Library(name="Shelfish", is_public=True)
    db.add(lib); db.flush()
    want = _book(db, make_book, "W", word_count=5_000)
    plain = _book(db, make_book, "P", word_count=5_000)
    finished = _book(db, make_book, "F", word_count=5_000)
    in_lib = _book(db, make_book, "L", word_count=5_000)
    in_lib.libraries.append(lib)
    _status(db, user.id, want.id, "want_to_read")
    _status(db, user.id, finished.id, "read")
    db.flush()

    assert client.get("/api/stats/backlog", params={"scope": "want"}).json()["books"] == 1
    assert client.get("/api/stats/backlog", params={"scope": "unread"}).json()["books"] == 3
    assert client.get("/api/stats/backlog", params={"scope": f"library:{lib.id}"}).json()["books"] == 1

    # A shelf is a saved filter: its params drive the scope, finished books are still excluded.
    shelf = SavedFilter(name="Fives", owner_id=user.id, params=json.dumps({"q": "", "sort": "title"}))
    db.add(shelf); db.flush()
    assert client.get("/api/stats/backlog", params={"scope": f"shelf:{shelf.id}"}).json()["books"] == 3

    assert client.get("/api/stats/backlog", params={"scope": "bogus"}).status_code == 422
    assert client.get("/api/stats/backlog", params={"scope": "library:999"}).status_code == 404
    del plain


def test_backlog_scopes_lists_libraries_and_shelves(client: TestClient, db, admin_user):
    user, _ = admin_user
    db.add(Library(name="Pile", is_public=True))
    db.add(SavedFilter(name="Winter", owner_id=user.id, params="{}"))
    db.flush()
    scopes = client.get("/api/stats/backlog-scopes").json()
    assert [s["id"] for s in scopes[:2]] == ["want", "unread"]
    assert any(s["label"] == "Pile" and s["group"] == "Libraries" for s in scopes)
    assert any(s["label"] == "Winter" and s["group"] == "Shelves" for s in scopes)
