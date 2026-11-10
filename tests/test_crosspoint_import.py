"""Import of a CrossPoint Sync account export (services/crosspoint_import.py).

The fixture archive is built the way the reference server builds it
(crosspoint-sync ``src/models/account-data.ts`` ``exportAccountData``): every
table in ``ACCOUNT_TABLES`` as a list of rows holding every column except
``user_id``, ``change_seq``, ``server_change_seq`` and ``revision``. The
columns of each section come from the server's migrations:

- documents: 0001_init + 0002 (filename), 0011 (status, status_at, cover_*),
  0012 (page_count), 0014_hardcover_details + 0015 (hc_*, series, ...),
  0017 (start_date, finished_date), 0019 (pause_reason), 0026/0033/0036
  (*_manual), 0034 (language), 0043 (isbn, user_rating), 0044 (pages_checked_at)
- document_aliases: 0007 + 0016 (merge_stats)
- progress: 0001; progress_samples: 0006; progress_log: 0012
- bookmarks, clippings: 0001 + 0010 (layout_signature, offsets)
- stats_device_global / stats_device_book: 0001 (a stats_device_book row is what
  ``PUT /api/v1/stats/books`` stored: payload = the parsed BookStatsSnapshot,
  updated_at = the server's time of that upload, src/routes/v1/stats.ts:214-261);
  stats_device_day: 0014;
  stats_device_book_day: 0035
- reading_sessions: 0030; reading_goals: 0048
Session rows are what ``PUT /api/v1/stats/sessions`` stored (src/routes/v1/stats.ts,
from the firmware's ReadingSyncUpload.cpp body); clipping rows what
``PUT /api/v1/clippings/:document`` stored (src/routes/v1/clippings.ts).

Titles and authors are invented.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.audit_log import AuditLog
from backend.models.book import Book
from backend.models.kosync import (
    KOSyncBookStats,
    KOSyncClipping,
    KOSyncDailyStats,
    KOSyncDeviceStats,
    KOSyncDocumentMap,
    KOSyncDocumentMeta,
    KOSyncSessionReceipt,
    KOSyncUser,
)
from backend.models.tome_sync import ReadingSession
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import crosspoint_import

PREVIEW = "/api/import/crosspoint/preview"
APPLY = "/api/import/crosspoint/apply"
DEVICE = "a1b2c3d4e5f60718"

DOC_LIB = "11111111111111111111111111111111"     # matches a library book
DOC_NEW = "22222222222222222222222222222222"     # becomes a file-less book
DOC_CANON = "33333333333333333333333333333333"   # merged book, canonical hash
DOC_ALIAS = "44444444444444444444444444444444"   # merged book, the other hash
DOC_BLANK = "55555555555555555555555555555555"   # sessions, no title anywhere
DOC_IDLE = "66666666666666666666666666666666"    # described, never read


def _ts(y, m, d, hh=0, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


FINISH_DAY = _ts(2025, 3, 2)          # a calendar date: UTC midnight (migration 0017)


def _document(document, title=None, author=None, filename=None, **extra):
    row = {
        "document": document, "title": title, "author": author, "filesize": 123456,
        "updated_at": _ts(2025, 3, 5), "filename": filename, "status": None, "status_at": None,
        "cover_url": None, "cover_checked_at": None, "page_count": 320,
        "hc_id": None, "hc_slug": None, "moods": None, "genres": None, "content_warnings": None,
        "rating": None, "series": "A Series Hardcover Guessed", "series_position": 7.0,
        "hc_series_id": None, "release_year": None, "hc_checked_at": None, "description": None,
        "start_date": None, "finished_date": None, "pause_reason": None, "meta_manual": 0,
        "genres_manual": 0, "moods_manual": 0, "content_warnings_manual": 0,
        "description_manual": 0, "series_manual": 0, "release_year_manual": 0,
        "language": "en", "isbn": None, "user_rating": None, "pages_checked_at": None,
    }
    row.update(extra)
    return row


def _session(sid, document, start, seconds, span=None, device=DEVICE):
    return {
        "device_id": device, "session_id": sid, "document": document, "started_at": start,
        "ended_at": start + (span if span is not None else seconds + 60), "seconds": seconds,
        "utc_offset_minutes": 60, "created_at": start + 4000,
    }


def _clip(document, cid, text="A line worth keeping.", deleted=0, updated=None, note=None):
    return {
        "document": document, "id": cid, "spine_index": 3, "start_page": 10, "end_page": 10,
        "page_count": 1, "start_word": 4, "end_word": 9, "word_count": 5, "paragraph_index": 2,
        "chapter_title": "Chapter Three", "text": "" if deleted else text, "note": note,
        "color": None, "created_at": _ts(2025, 2, 1), "deleted": deleted,
        "updated_at": updated or _ts(2025, 2, 1), "layout_signature": 77,
        "start_offset": None, "end_offset": None,
    }


def _stats_payload(**over):
    p = {"v": 5, "sessions": 3, "seconds": 3600, "pages": 80, "completed": False, "avg_fwd": 20,
         "pace_n": 3, "eta": 0, "start_manual": False, "finish_manual": False,
         "start_date": 0, "finished_date": 0, "tod": [0, 0, 0, 0], "dow": [0] * 7}
    p.update(over)
    return json.dumps(p)


def build_archive():
    """A realistic export: five books with reading data, one alias, one idle file."""
    sessions = [
        _session("a0" * 16, DOC_LIB, _ts(2025, 1, 10, 20), 1800),
        _session("a1" * 16, DOC_LIB, _ts(2025, 1, 11, 21), 2400),
        _session("a2" * 16, DOC_LIB, _ts(2025, 3, 1, 19), 3000),
        _session("b0" * 16, DOC_NEW, _ts(2025, 2, 3, 8), 900),
        _session("c0" * 16, DOC_CANON, _ts(2025, 2, 5, 22), 1200),
        _session("c1" * 16, DOC_ALIAS, _ts(2025, 2, 6, 22), 600),   # the other hash of the same book
        _session("d0" * 16, DOC_BLANK, _ts(2025, 2, 7, 12), 700),
        _session("e0" * 16, DOC_NEW, _ts(2025, 2, 8, 9), 5),        # too short: the live rules refuse it
    ]
    return {
        "format": "crosspoint-sync-account",
        "version": 1,
        "exported_at": "2025-03-10T18:00:00.000Z",
        "profile": {"username": "reader-on-crosspoint", "display_name": None,
                    "auto_pause_days": 30, "public_profile": False},
        "data": {
            "documents": [
                _document(DOC_LIB, "The Glass Orchard", "Ilse Varga", "glass_orchard.epub",
                          status="finished", status_at=_ts(2025, 3, 3), finished_date=FINISH_DAY),
                _document(DOC_NEW, "Salt and Lantern", "Odo Fenwick", "salt_and_lantern.epub"),
                _document(DOC_CANON, "Harbor of Small Hours", "Nell Arden", "harbor.epub",
                          status="dnf", status_at=_ts(2025, 2, 9)),
                _document(DOC_IDLE, "A Field Guide to Nothing", "Pim Oster", "nothing.epub"),
            ],
            "document_aliases": [
                {"alias": DOC_ALIAS, "document": DOC_CANON, "created_at": _ts(2025, 2, 6), "merge_stats": 1},
            ],
            "progress": [
                {"document": DOC_LIB, "device_id": DEVICE, "device": "Xteink X4", "percentage": 1.0,
                 "progress": "/body/DocFragment[40]", "position": None, "updated_at": _ts(2025, 3, 1, 21)},
                {"document": DOC_NEW, "device_id": DEVICE, "device": "Xteink X4", "percentage": 0.45,
                 "progress": "/body/DocFragment[9]", "position": None, "updated_at": _ts(2025, 2, 3, 9)},
                {"document": DOC_CANON, "device_id": DEVICE, "device": "Xteink X4", "percentage": 0.3,
                 "progress": "/body/DocFragment[5]", "position": None, "updated_at": _ts(2025, 2, 6, 23)},
            ],
            "progress_samples": [
                {"document": DOC_NEW, "pct_bucket": 450, "percentage": 0.45,
                 "progress": "/body/DocFragment[9]", "position": None, "updated_at": _ts(2025, 2, 3, 9)},
            ],
            "progress_log": [
                {"document": DOC_LIB, "device_id": DEVICE, "percentage": 0.5, "at": _ts(2025, 1, 11, 22)},
                {"document": DOC_LIB, "device_id": DEVICE, "percentage": 1.0, "at": _ts(2025, 3, 1, 21)},
            ],
            "bookmarks": [
                {"document": DOC_NEW, "id": "bm1", "xpath": "/body/DocFragment[9]", "percentage": 0.45,
                 "summary": "", "spine_index": 9, "paragraph_count": None, "paragraph_pos": None,
                 "deleted": 0, "updated_at": _ts(2025, 2, 3, 9)},
            ],
            "clippings": [
                _clip(DOC_LIB, "clip-lib-1", text="Orchards remember every frost."),
                _clip(DOC_NEW, "clip-new-gone", deleted=1),
                _clip(DOC_ALIAS, "clip-merged", text="old copy", updated=_ts(2025, 2, 6)),
                _clip(DOC_CANON, "clip-merged", text="The harbor kept its own hours.",
                      updated=_ts(2025, 2, 7), note="read again"),
            ],
            "stats_device_global": [
                {"device_id": DEVICE, "device": "Xteink X4",
                 "payload": json.dumps({"v": 5, "sessions": 9, "seconds": 11200, "pages": 400,
                                        "completed": 1, "tod": [1, 2, 3, 4], "dow": [1, 2, 3, 4, 5, 6, 7],
                                        "anchor_day": 9200, "history_b64": "AAAA", "streak": 3}),
                 "updated_at": _ts(2025, 3, 5)},
            ],
            "stats_device_book": [
                {"device_id": DEVICE, "document": DOC_LIB,
                 "payload": _stats_payload(completed=True, finished_date=FINISH_DAY), "updated_at": _ts(2025, 3, 5)},
                # The merged book: one snapshot under each of its hashes.
                {"device_id": DEVICE, "document": DOC_CANON,
                 "payload": _stats_payload(pages=50, seconds=1000), "updated_at": _ts(2025, 2, 6)},
                {"device_id": DEVICE, "document": DOC_ALIAS,
                 "payload": _stats_payload(pages=120, seconds=2400), "updated_at": _ts(2025, 2, 7)},
            ],
            "stats_device_day": [
                {"device_id": DEVICE, "date": "2025-01-10", "seconds": 1800, "updated_at": _ts(2025, 1, 11)},
                {"device_id": DEVICE, "date": "2025-01-11", "seconds": 2400, "updated_at": _ts(2025, 1, 12)},
                {"device_id": DEVICE, "date": "2025-02-03", "seconds": 900, "updated_at": _ts(2025, 2, 4)},
            ],
            "stats_device_book_day": [
                {"device_id": DEVICE, "document": DOC_LIB, "date": "2025-01-10", "seconds": 1800,
                 "updated_at": _ts(2025, 1, 11)},
            ],
            "reading_sessions": sessions,
            "reading_goals": [{"year": 2025, "target": 24}],
        },
    }


def _bytes(archive) -> bytes:
    return json.dumps(archive).encode()


def _post(client, url, archive=None, raw=None, headers=None):
    body = raw if raw is not None else _bytes(archive if archive is not None else build_archive())
    return client.post(url, files={"file": ("crosspoint-export.json", body, "application/json")},
                       headers=headers or {})


def _snapshot(db, user_id=None):
    """Row counts of everything the import may write."""
    def count(model, column=None):
        q = db.query(model)
        if user_id is not None and column is not None:
            q = q.filter(column == user_id)
        return q.count()
    return {
        "books": db.query(Book).count(),
        "maps": count(KOSyncDocumentMap, KOSyncDocumentMap.tome_user_id),
        "metas": count(KOSyncDocumentMeta, KOSyncDocumentMeta.user_id),
        "sessions": count(ReadingSession, ReadingSession.user_id),
        "receipts": count(KOSyncSessionReceipt, KOSyncSessionReceipt.user_id),
        "statuses": count(UserBookStatus, UserBookStatus.user_id),
        "clippings": count(KOSyncClipping, KOSyncClipping.user_id),
        "daily": count(KOSyncDailyStats, KOSyncDailyStats.user_id),
        "devices": count(KOSyncDeviceStats, KOSyncDeviceStats.user_id),
        "book_stats": count(KOSyncBookStats, KOSyncBookStats.user_id),
        "audit": db.query(AuditLog).filter(AuditLog.action == "kosync.crosspoint_import").count(),
    }


@pytest.fixture()
def library_book(make_book):
    return make_book(title="The Glass Orchard", author="Ilse Varga")


def _map(db, user_id, document):
    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user_id, document=document).first()
    return m.book_id if m else None


# ── preview and import ────────────────────────────────────────────────────────

def test_preview_writes_nothing(db, client, admin_user, library_book):
    user, _ = admin_user
    before = _snapshot(db)
    r = _post(client, PREVIEW)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["applied"] is False and report["nothing_new"] is False
    assert report["sessions"]["counts"] == {"created": 7}
    assert _snapshot(db) == before
    assert db.query(Book).filter(Book.title == "Salt and Lantern").count() == 0


def test_import_does_what_the_preview_said(db, client, admin_user, library_book):
    preview = _post(client, PREVIEW).json()
    result = _post(client, APPLY)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["applied"] is True
    for key in ("books", "sessions", "statuses", "clippings", "daily", "devices", "progress", "book_pages",
                "seconds_added", "first_session", "last_session", "not_imported"):
        assert result[key] == preview[key], key
    strip = lambda rows: [{k: v for k, v in r.items() if k != "book_id"} for r in rows]  # noqa: E731
    assert strip(result["book_list"]) == strip(preview["book_list"])
    assert result["text"].splitlines()[1:] == preview["text"].splitlines()[1:]

    assert result["sessions"]["counts"] == {"created": 7}
    assert result["sessions"]["skip_reasons"][0]["reason"] == "shorter than 10 seconds of reading"
    assert result["sessions"]["skip_reasons"][0]["examples"][0]["session_id"] == "e0" * 16
    assert result["seconds_added"] == 1800 + 2400 + 3000 + 900 + 1200 + 600 + 700
    assert result["first_session"].startswith("2025-01-10") and result["last_session"].startswith("2025-03-01")
    assert result["books"]["counts"] == {"matched": 1, "created": 2}
    reasons = {r["reason"]: r["count"] for r in result["books"]["skip_reasons"]}
    assert reasons == {"left unlinked: the export has no title or file name for this file": 1,
                       "no reading recorded for this file": 1}
    sections = {n["section"] for n in result["not_imported"]}
    assert {"bookmarks", "progress_samples", "stats_device_book_day", "reading_goals",
            "progress_log"} <= sections
    assert "stats_device_book" not in sections


def test_a_second_import_changes_nothing(db, client, admin_user, library_book):
    assert _post(client, APPLY).status_code == 200
    after_first = _snapshot(db)
    preview = _post(client, PREVIEW).json()
    assert preview["nothing_new"] is True
    assert "Nothing new" in preview["text"]
    second = _post(client, APPLY).json()
    assert second["nothing_new"] is True
    assert second["sessions"]["counts"] == {"unchanged": 7}
    assert second["seconds_added"] == 0
    assert second["clippings"]["counts"].get("created", 0) == 0
    snap = _snapshot(db)
    assert snap.pop("audit") == after_first.pop("audit") + 1   # each import is audited
    assert snap == after_first


# ── books ─────────────────────────────────────────────────────────────────────

def test_books_link_to_library_become_file_less_and_aliases_join(db, client, admin_user, library_book):
    user, _ = admin_user
    report = _post(client, APPLY).json()
    assert _map(db, user.id, DOC_LIB) == library_book.id

    salt = db.query(Book).filter(Book.title == "Salt and Lantern").one()
    assert salt.status == "no_file" and salt.author == "Odo Fenwick" and salt.added_by == user.id
    # The Hardcover series guess in the export is not a reader's metadata.
    assert salt.series is None and salt.series_index is None
    assert _map(db, user.id, DOC_NEW) == salt.id

    harbor = db.query(Book).filter(Book.title == "Harbor of Small Hours").one()
    assert _map(db, user.id, DOC_CANON) == harbor.id == _map(db, user.id, DOC_ALIAS)
    assert _map(db, user.id, DOC_BLANK) is None and _map(db, user.id, DOC_IDLE) is None
    assert db.query(Book).filter(Book.title == "A Field Guide to Nothing").count() == 0

    rows = {r["document"]: r for r in report["book_list"]}
    assert rows[DOC_LIB]["outcome"] == "matched" and rows[DOC_LIB]["book_id"] == library_book.id
    assert rows[DOC_CANON]["outcome"] == "created" and sorted(rows[DOC_CANON]["documents"]) == [DOC_CANON, DOC_ALIAS]
    assert rows[DOC_CANON]["sessions_new"] == 2 and rows[DOC_CANON]["seconds_new"] == 1800
    assert rows[DOC_BLANK]["outcome"] == "unlinked"
    assert DOC_IDLE not in rows
    created = db.query(AuditLog).filter(AuditLog.action == "books.created_from_reader").count()
    assert created == 2


def test_sessions_land_on_their_books_and_show_in_stats_and_calendar(db, client, admin_user, library_book):
    user, _ = admin_user
    _post(client, APPLY)
    by_book = {}
    for s in db.query(ReadingSession).filter_by(user_id=user.id):
        by_book.setdefault(s.book_id, []).append(s.duration_seconds)
    harbor = db.query(Book).filter(Book.title == "Harbor of Small Hours").one()
    assert sorted(by_book[library_book.id]) == [1800, 2400, 3000]
    assert sorted(by_book[harbor.id]) == [600, 1200]
    assert by_book[None] == [700]          # stored, not linked
    s = db.query(ReadingSession).filter_by(duration_seconds=1800, book_id=library_book.id).one()
    assert s.started_at == datetime(2025, 1, 10, 20) and s.ended_at == datetime(2025, 1, 10, 20, 31)
    assert s.device == "Xteink X4"
    receipt = db.query(KOSyncSessionReceipt).filter_by(reading_session_id=s.id).one()
    assert (receipt.session_uuid, receipt.device_id, receipt.document) == ("a0" * 16, DEVICE, DOC_LIB)

    listed = client.get("/api/stats/sessions?limit=50").json()
    listed = listed["sessions"] if isinstance(listed, dict) else listed
    assert sorted(r["duration_seconds"] for r in listed) == [600, 700, 900, 1200, 1800, 2400, 3000]
    days = client.get("/api/stats/calendar?month=2025-01").json()["days"]
    assert sum(d["seconds"] for d in days) == 1800 + 2400


def test_statuses_and_finish_dates(db, client, admin_user, library_book):
    user, _ = admin_user
    report = _post(client, APPLY).json()
    lib = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=library_book.id).one()
    assert lib.status == "read" and lib.progress_pct == 1.0
    assert lib.finished_at == datetime(2025, 3, 2, 12)   # the calendar date, at noon UTC
    salt = db.query(Book).filter(Book.title == "Salt and Lantern").one()
    s = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=salt.id).one()
    assert (s.status, s.progress_pct, s.finished_at) == ("reading", 0.45, None)
    harbor = db.query(Book).filter(Book.title == "Harbor of Small Hours").one()
    h = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=harbor.id).one()
    assert (h.status, h.progress_pct) == ("shelved", 0.3)   # "did not finish" in CrossPoint Sync
    assert report["statuses"]["counts"] == {"finished": 1, "progress": 1, "shelved": 1}


def test_existing_progress_and_finish_are_never_lowered(db, client, admin_user, library_book, make_book):
    user, _ = admin_user
    salt = make_book(title="Salt and Lantern", author="Odo Fenwick")
    db.add(UserBookStatus(user_id=user.id, book_id=library_book.id, status="read", progress_pct=1.0,
                          finished_at=datetime(2024, 12, 24, 9)))
    db.add(UserBookStatus(user_id=user.id, book_id=salt.id, status="reading", progress_pct=0.8))
    db.flush()
    report = _post(client, APPLY).json()
    lib = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=library_book.id).one()
    assert lib.finished_at == datetime(2024, 12, 24, 9)
    s = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=salt.id).one()
    assert (s.status, s.progress_pct) == ("reading", 0.8)
    assert report["statuses"]["counts"]["kept"] == 2
    rows = {r["document"]: r for r in report["book_list"]}
    assert rows[DOC_NEW]["status_change"] == "status kept: Tome has reading at 80%"


def test_a_finish_without_a_date_takes_the_progress_history(db, client, admin_user, library_book):
    user, _ = admin_user
    archive = build_archive()
    doc = archive["data"]["documents"][0]
    doc["finished_date"] = None
    doc["status"] = None
    archive["data"]["stats_device_book"] = []
    _post(client, APPLY, archive)
    lib = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=library_book.id).one()
    # First progress-log entry at or past 98% (crosspoint-sync activity.ts FINISHED_AT).
    assert lib.status == "read" and lib.finished_at == datetime(2025, 3, 1, 21)


# ── clippings ─────────────────────────────────────────────────────────────────

def test_clippings_show_in_highlights(db, client, admin_user, library_book):
    user, _ = admin_user
    report = _post(client, APPLY).json()
    assert report["clippings"]["counts"] == {"merged_copy": 1, "created": 2, "deleted": 1}
    r = client.get("/api/annotations?include_clippings=1")
    assert r.status_code == 200, r.text
    text = json.dumps(r.json())
    assert "Orchards remember every frost." in text
    assert "The harbor kept its own hours." in text and "old copy" not in text
    rows = {c.clip_id: c for c in db.query(KOSyncClipping).filter_by(user_id=user.id)}
    assert rows["clip-new-gone"].deleted and rows["clip-new-gone"].text is None
    assert rows["clip-merged"].document == DOC_CANON and rows["clip-merged"].note == "read again"
    assert len({c.revision for c in rows.values()}) == 3


def test_a_clipping_tome_already_has_is_left_alone(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(KOSyncClipping(user_id=user.id, document=DOC_LIB, clip_id="clip-lib-1", revision=1,
                          deleted=False, text="what the reader sent Tome", updated_at=1))
    db.flush()
    report = _post(client, APPLY).json()
    assert report["clippings"]["counts"]["already"] == 1
    row = db.query(KOSyncClipping).filter_by(user_id=user.id, clip_id="clip-lib-1").one()
    assert row.text == "what the reader sent Tome"


# ── the reader after the import ───────────────────────────────────────────────

def test_a_session_the_reader_sends_again_is_not_counted_twice(db, client, admin_user, library_book):
    user, _ = admin_user
    acc = KOSyncUser(username="ks-import", userkey="k" * 32, user_id=user.id)
    db.add(acc)
    db.flush()
    _post(client, APPLY)
    s = build_archive()["data"]["reading_sessions"][0]
    item = {k: s[k] for k in ("session_id", "document", "started_at", "ended_at", "seconds", "utc_offset_minutes")}
    r = client.put("/api/v1/api/v1/stats/sessions", headers={"x-auth-user": acc.username, "x-auth-key": acc.userkey},
                   json={"device_id": DEVICE, "items": [item]})
    assert r.status_code == 200 and r.json()["accepted_session_ids"] == [s["session_id"]]
    assert db.query(ReadingSession).filter_by(user_id=user.id).count() == 7
    assert db.query(ReadingSession).filter_by(book_id=library_book.id, duration_seconds=1800).count() == 1


def test_existing_tome_data_is_kept(db, client, admin_user, library_book, make_book):
    """A link the user made wins, a session they trimmed stays trimmed, one
    they deleted stays deleted."""
    user, _ = admin_user
    other = make_book(title="A Different Edition", author="Someone Else")
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=DOC_NEW, book_id=other.id))
    trimmed = ReadingSession(user_id=user.id, book_id=library_book.id, started_at=datetime(2025, 1, 10, 20),
                             ended_at=datetime(2025, 1, 10, 20, 31), duration_seconds=1000, device="Xteink X4")
    db.add(trimmed)
    db.flush()
    db.add(KOSyncSessionReceipt(user_id=user.id, device_id=DEVICE, session_uuid="a0" * 16, document=DOC_LIB,
                                started_at=datetime(2025, 1, 10, 20), ended_at=datetime(2025, 1, 10, 20, 31),
                                seconds=1800, reading_session_id=trimmed.id))
    db.add(KOSyncSessionReceipt(user_id=user.id, device_id=DEVICE, session_uuid="a1" * 16, document=DOC_LIB,
                                started_at=datetime(2025, 1, 11, 21), ended_at=datetime(2025, 1, 11, 21, 41),
                                seconds=2400, reading_session_id=None))
    db.flush()
    report = _post(client, APPLY).json()
    assert _map(db, user.id, DOC_NEW) == other.id
    assert db.query(Book).filter(Book.title == "Salt and Lantern").count() == 0
    assert db.get(ReadingSession, trimmed.id).duration_seconds == 1000
    assert db.query(ReadingSession).filter_by(user_id=user.id, book_id=library_book.id).count() == 2
    assert report["sessions"]["counts"] == {"created": 5, "unchanged": 2}
    rows = {r["document"]: r for r in report["book_list"]}
    assert rows[DOC_NEW]["outcome"] == "already_linked" and rows[DOC_NEW]["book_id"] == other.id


def test_an_id_tome_has_for_another_session_is_skipped(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(KOSyncSessionReceipt(user_id=user.id, device_id=DEVICE, session_uuid="b0" * 16, document=DOC_LIB,
                                started_at=datetime(2024, 5, 1), ended_at=datetime(2024, 5, 1, 1),
                                seconds=600, reading_session_id=None))
    db.flush()
    report = _post(client, APPLY).json()
    reasons = {r["reason"]: r for r in report["sessions"]["skip_reasons"]}
    assert reasons["Tome already has a different session under this id"]["examples"][0]["session_id"] == "b0" * 16


def test_another_users_data_is_untouched(db, client, admin_user, library_book):
    user, _ = admin_user
    other = User(username="neighbour", email="n@example.com", hashed_password=hash_password("pw"),
                 is_active=True, role="member", must_change_password=False)
    db.add(other)
    db.flush()
    db.add(KOSyncDocumentMap(tome_user_id=other.id, document=DOC_NEW, book_id=library_book.id))
    db.add(KOSyncBookStats(user_id=other.id, device_id=DEVICE, document=DOC_LIB, pages=7, seconds=70,
                           snapshot="{}", updated_at=datetime(2026, 1, 1)))
    db.add(KOSyncSessionReceipt(user_id=other.id, device_id=DEVICE, session_uuid="b0" * 16, document=DOC_NEW,
                                started_at=datetime(2025, 2, 3, 8), ended_at=datetime(2025, 2, 3, 8, 16),
                                seconds=900, reading_session_id=None))
    db.flush()
    before = _snapshot(db, other.id)
    # The file names another user; it cannot select them.
    archive = build_archive()
    archive["profile"]["username"] = "neighbour"
    report = _post(client, APPLY, archive).json()
    assert report["sessions"]["counts"] == {"created": 7}
    # The neighbour's link of the same file is theirs, not the admin's.
    salt = db.query(Book).filter(Book.title == "Salt and Lantern").one()
    assert _map(db, user.id, DOC_NEW) == salt.id
    assert {r["document"]: r["outcome"] for r in report["book_list"]}[DOC_NEW] == "created"
    after = _snapshot(db, other.id)
    for key in ("maps", "metas", "sessions", "receipts", "statuses", "clippings", "daily", "devices",
                "book_stats"):
        assert after[key] == before[key], key
    assert _map(db, other.id, DOC_NEW) == library_book.id
    assert db.query(KOSyncBookStats).filter_by(user_id=other.id).one().pages == 7
    assert db.query(ReadingSession).filter_by(user_id=other.id).count() == 0

    # And the neighbour's own import does not see the admin's.
    token = create_access_token(subject=other.id)
    r = _post(client, PREVIEW, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["sessions"]["counts"]["created"] == 6   # "b0..." is already the neighbour's
    rows = {x["document"]: x for x in r.json()["book_list"]}
    assert rows[DOC_NEW]["outcome"] == "already_linked"


def test_a_guest_cannot_import(db, client):
    guest = User(username="guest1", email="g@example.com", hashed_password=hash_password("pw"),
                 is_active=True, role="guest", must_change_password=False)
    db.add(guest)
    db.flush()
    token = create_access_token(subject=guest.id)
    r = _post(client, PREVIEW, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_the_import_is_audited_with_counts_only(db, client, admin_user, library_book):
    _post(client, APPLY)
    row = db.query(AuditLog).filter(AuditLog.action == "kosync.crosspoint_import").one()
    details = json.loads(row.details)
    assert details["sessions"] == {"created": 7} and details["seconds_added"] == 10600
    assert "Glass Orchard" not in row.details and "reader-on-crosspoint" not in row.details


# ── refusals ──────────────────────────────────────────────────────────────────

def _mutate(fn):
    archive = build_archive()
    fn(archive)
    return _bytes(archive)


REFUSALS = {
    "not json": (b"this is not json", 400, "not valid JSON"),
    "truncated": (_bytes(build_archive())[:-200], 400, "cut off"),
    "empty": (b"   ", 400, "empty"),
    "not utf-8": (b"\xff\xfe{}", 400, "UTF-8"),
    "a list": (b"[1, 2]", 400, "a list where an object is expected"),
    "wrong format": (_mutate(lambda a: a.update(format="koreader-stats")), 400, '"format"'),
    "wrong version": (_mutate(lambda a: a.update(version=2)), 400, "version 2"),
    "no data": (_mutate(lambda a: a.pop("data")), 400, '"data"'),
    "section not a list": (_mutate(lambda a: a["data"].update(reading_sessions={"x": 1})), 400,
                           'Section "reading_sessions" should be a list'),
    "row not an object": (_mutate(lambda a: a["data"]["progress"].append("oops")), 400,
                          'Section "progress", row 3: expected an object'),
    "wrong type": (_mutate(lambda a: a["data"]["reading_sessions"][2].update(started_at="yesterday")), 400,
                   'Section "reading_sessions", row 2, field "started_at": expected a whole number, found text'),
    "missing field": (_mutate(lambda a: a["data"]["clippings"][1].pop("id")), 400,
                      'Section "clippings", row 1, field "id": missing'),
    "NaN": (_bytes(build_archive()).replace(b'"percentage": 0.45', b'"percentage": NaN'), 400, "NaN"),
    "alias cycle": (_mutate(lambda a: a["data"]["document_aliases"].append(
        {"alias": DOC_CANON, "document": DOC_ALIAS, "created_at": 1, "merge_stats": 1})), 400, "cycle"),
}


@pytest.mark.parametrize("name", list(REFUSALS))
def test_a_bad_file_is_refused_with_where_and_nothing_written(db, client, library_book, name):
    raw, status, needle = REFUSALS[name]
    before = _snapshot(db)
    for url in (PREVIEW, APPLY):
        r = _post(client, url, raw=raw)
        assert r.status_code == status, (name, r.text)
        assert needle in r.json()["detail"], r.json()["detail"]
    assert _snapshot(db) == before


def test_an_oversized_file_is_refused(db, client, library_book, monkeypatch):
    monkeypatch.setattr(crosspoint_import, "MAX_BYTES", 1000)
    r = _post(client, APPLY)
    assert r.status_code == 413 and "limit" in r.json()["detail"]
    monkeypatch.setattr(crosspoint_import, "MAX_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(crosspoint_import, "MAX_RECORDS", 10)
    r = _post(client, APPLY)
    assert r.status_code == 413 and "records" in r.json()["detail"]
    assert db.query(ReadingSession).count() == 0


def test_unknown_sections_and_columns_are_reported_not_fatal(db, client, library_book):
    def change(a):
        a["data"]["future_things"] = [{"x": 1}, {"x": 2}]
        a["data"]["documents"][0]["shiny_new_column"] = "?"
        a["data"].pop("bookmarks")
    report = _post(client, APPLY, raw=_mutate(change)).json()
    assert report["file"]["unknown_sections"] == {"future_things": 2}
    assert report["file"]["unknown_columns"] == {"documents": ["shiny_new_column"]}
    assert report["sessions"]["counts"] == {"created": 7}
    assert "future_things (2 rows)" in report["text"] and "documents.shiny_new_column" in report["text"]
    assert "bookmarks" not in report["file"]["sections"]


def test_rows_the_live_rules_refuse_are_skipped_and_counted(db, client, library_book):
    def change(a):
        sessions = a["data"]["reading_sessions"]
        sessions.append(_session("f0" * 16, DOC_LIB, _ts(2025, 1, 20), 900, span=300))   # longer than its span
        sessions.append(_session("f1" * 16, DOC_LIB, int(time.time()) + 5 * 86400, 900))  # in the future
        sessions.append(_session("f2" * 16, DOC_LIB, _ts(2025, 1, 21), 900, device="bad device!"))
        sessions.append(dict(sessions[0]))                                                 # twice in the file
        a["data"]["clippings"].append(_clip(DOC_LIB, "too-long", text="x" * 5000))
        a["data"]["stats_device_day"].append({"device_id": DEVICE, "date": "2025-13-40", "seconds": 5,
                                              "updated_at": 1})
    report = _post(client, APPLY, raw=_mutate(change)).json()
    reasons = {r["reason"]: r["count"] for r in report["sessions"]["skip_reasons"]}
    assert reasons == {
        "shorter than 10 seconds of reading": 1,
        "reading time longer than the session, or a session over 7 days": 1,
        "ends in the future (reader clock was wrong)": 1,
        "device id Tome cannot store (1-64 letters, digits, - or _)": 1,
        "the same session id appears twice in the file": 1,
    }
    assert report["sessions"]["counts"] == {"created": 7}
    assert report["clippings"]["skip_reasons"][0]["reason"] == "no text, or text longer than 4096 bytes"
    assert report["daily"]["skip_reasons"][0]["reason"] == "invalid daily date"
    assert "Skipped" in report["text"] and "f0" * 16 in report["text"]


def test_a_failure_late_in_the_import_writes_nothing(db, client, admin_user, library_book, monkeypatch):
    before = _snapshot(db)
    from backend.services import kosync_clippings

    real_store = kosync_clippings.store
    calls = []

    def failing_store(*args, **kwargs):
        calls.append(1)
        real_store(*args, **kwargs)
        if len(calls) == 3:
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(kosync_clippings, "store", failing_store)
    r = _post(client, APPLY)
    assert r.status_code == 500 and "nothing was written" in r.json()["detail"]
    assert calls == [1, 1, 1]
    assert _snapshot(db) == before
    assert db.query(Book).filter(Book.title.in_(["Salt and Lantern", "Harbor of Small Hours"])).count() == 0
    # And it works once the cause is gone.
    monkeypatch.setattr(kosync_clippings, "store", real_store)
    assert _post(client, APPLY).status_code == 200
    assert _snapshot(db)["sessions"] == 7


def test_the_text_report_names_books_and_reasons(db, client, library_book):
    text = _post(client, PREVIEW).json()["text"]
    assert text.startswith("Tome preview of a CrossPoint Sync account export (nothing written yet)")
    assert 'by user "reader-on-crosspoint"' in text
    assert f'"The Glass Orchard" by Ilse Varga [{DOC_LIB}]: linked to library book #{library_book.id}' in text
    assert f"[{DOC_CANON} + 1 merged]: new book without a file" in text
    assert "left unlinked: the export has no title or file name for this file" in text
    assert "bookmarks (1 row)" in text


def test_the_archive_matches_the_reference_server_shape():
    """Every section the reference exporter writes (account-data.ts
    ACCOUNT_TABLES) is in the fixture, and Tome knows all of them."""
    tables = ['documents', 'document_aliases', 'progress', 'progress_samples', 'progress_log',
              'bookmarks', 'clippings', 'stats_device_global', 'stats_device_book',
              'stats_device_day', 'stats_device_book_day', 'reading_sessions', 'reading_goals']
    data = build_archive()["data"]
    assert sorted(data) == sorted(tables)
    assert set(tables) == set(crosspoint_import.SECTIONS)
    archive = crosspoint_import.parse(_bytes(build_archive()))
    assert archive.unknown_columns == {} and archive.unknown_sections == {}
    assert copy.deepcopy(archive.counts)["reading_sessions"] == 8


# ── per-book page counters ────────────────────────────────────────────────────

def _own(client, book_id):
    r = client.get(f"/api/books/{book_id}/reading-stats")
    assert r.status_code == 200, r.text
    return r.json()["own"]


def test_reader_page_counters_give_pages_and_pace(db, client, admin_user, library_book):
    user, _ = admin_user
    report = _post(client, APPLY).json()
    assert report["book_pages"]["counts"] == {"created": 3}
    own = _own(client, library_book.id)
    assert own["pages_turned"] == 80
    assert own["pace_pages_per_min"] == pytest.approx(80 / 60, abs=0.01)       # 80 pages over the reader's 3600 s
    harbor = db.query(Book).filter(Book.title == "Harbor of Small Hours").one()
    # One snapshot under each hash of the merged book: each counted once.
    own = _own(client, harbor.id)
    assert own["pages_turned"] == 170
    assert own["pace_pages_per_min"] == pytest.approx(170 / (3400 / 60), abs=0.01)
    rows = {r.document: r for r in db.query(KOSyncBookStats).filter_by(user_id=user.id)}
    assert set(rows) == {DOC_LIB, DOC_CANON, DOC_ALIAS}
    assert rows[DOC_ALIAS].updated_at == datetime(2025, 2, 7)        # the export's own upload time
    book_rows = {r["document"]: r for r in report["book_list"]}
    assert book_rows[DOC_CANON]["reader_pages"] == 170
    assert "pages: 80 from the reader's counter" in report["text"]
    assert "Book page counts: 3 reader snapshots in the file: 3 new" in report["text"]
    # A second import changes nothing.
    again = _post(client, APPLY).json()
    assert again["book_pages"]["counts"] == {"already": 3} and again["nothing_new"] is True
    assert _own(client, harbor.id)["pages_turned"] == 170


def test_a_newer_live_snapshot_is_kept_and_an_older_one_replaced(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(KOSyncBookStats(user_id=user.id, device_id=DEVICE, document=DOC_LIB, pages=500, seconds=9000,
                           snapshot='{"pages":500}', updated_at=datetime(2026, 6, 1)))   # live, after the export
    db.add(KOSyncBookStats(user_id=user.id, device_id=DEVICE, document=DOC_CANON, pages=10, seconds=100,
                           snapshot='{"pages":10}', updated_at=datetime(2025, 1, 1)))   # older than the export's
    db.flush()
    report = _post(client, APPLY).json()
    assert report["book_pages"]["counts"] == {"already": 1, "replaced": 1, "created": 1}
    rows = {r.document: r for r in db.query(KOSyncBookStats).filter_by(user_id=user.id)}
    assert (rows[DOC_LIB].pages, rows[DOC_LIB].seconds) == (500, 9000)
    assert (rows[DOC_CANON].pages, rows[DOC_CANON].seconds) == (50, 1000)
    assert _own(client, library_book.id)["pages_turned"] == 500


def test_stats_kept_apart_in_crosspoint_sync_are_not_counted(db, client, library_book):
    archive = build_archive()
    archive["data"]["document_aliases"][0]["merge_stats"] = 0
    report = _post(client, APPLY, archive).json()
    assert report["book_pages"]["skip_reasons"][0]["reason"] == "merged in CrossPoint Sync with its stats kept apart"
    harbor = db.query(Book).filter(Book.title == "Harbor of Small Hours").one()
    assert _own(client, harbor.id)["pages_turned"] == 50


# ── progress from a service ───────────────────────────────────────────────────

def test_a_finish_from_a_service_position_says_so(db, client, admin_user, library_book):
    archive = build_archive()
    archive["data"]["progress"].append(
        {"document": DOC_NEW, "device_id": "kindle", "device": "Kindle", "percentage": 1.0,
         "progress": "kindle:1000000", "position": None, "updated_at": _ts(2025, 2, 20)})
    report = _post(client, APPLY, archive).json()
    rows = {r["document"]: r for r in report["book_list"]}
    assert rows[DOC_NEW]["status_change"].startswith("marked finished")
    assert rows[DOC_NEW]["source_note"] == 'the furthest position (100%) comes from the service "kindle" (Kindle), not a reader'
    assert 'service "kindle" (Kindle)' in report["text"]
    # Positions from the reader carry no note.
    assert rows[DOC_CANON]["source_note"] is None and rows[DOC_LIB]["source_note"] is None


def test_a_finish_from_a_service_progress_history_says_so(db, client, admin_user, library_book):
    archive = build_archive()
    doc = archive["data"]["documents"][0]
    doc["finished_date"] = None
    doc["status"] = None
    archive["data"]["stats_device_book"] = []
    archive["data"]["progress"][0]["percentage"] = 0.5
    archive["data"]["progress_log"][1]["device_id"] = "audiobookshelf"
    report = _post(client, APPLY, archive).json()
    row = {r["document"]: r for r in report["book_list"]}[DOC_LIB]
    assert row["status_change"].startswith("marked finished")
    assert row["source_note"] == 'the finish comes from the progress history of the service "audiobookshelf", not a reader'


# ── one clipping under two hashes of one book ─────────────────────────────────

def _clip_row(user_id, document, clip_id, text, updated_at, revision, deleted=False):
    return KOSyncClipping(user_id=user_id, document=document, clip_id=clip_id, revision=revision,
                          deleted=deleted, text=None if deleted else text, updated_at=updated_at,
                          created_at=1735689600)


def _shown(client, book_id):
    every = client.get("/api/annotations?include_clippings=1").json()
    text = json.dumps(every)
    per_book = client.get(f"/api/books/{book_id}/annotations?include_clippings=true").json()
    return text, [a.get("highlighted_text") for a in per_book]


def test_a_clipping_under_two_hashes_of_a_book_shows_once(db, client, admin_user, library_book):
    user, _ = admin_user
    for doc in (DOC_CANON, DOC_ALIAS):
        db.add(KOSyncDocumentMap(tome_user_id=user.id, document=doc, book_id=library_book.id))
    db.add(_clip_row(user.id, DOC_CANON, "same-id", "older copy", 100, 1))
    db.add(_clip_row(user.id, DOC_ALIAS, "same-id", "newer copy", 200, 2))
    db.add(_clip_row(user.id, DOC_ALIAS, "other-id", "another clipping", 150, 3))
    db.flush()
    text, per_book = _shown(client, library_book.id)
    assert "newer copy" in text and "older copy" not in text and text.count("another clipping") == 1
    assert sorted(per_book) == ["another clipping", "newer copy"]
    # The reader's own download per document is unchanged: each hash still has its copy.
    acc = KOSyncUser(username="ks-dup", userkey="k" * 32, user_id=user.id)
    db.add(acc)
    db.flush()
    headers = {"x-auth-user": acc.username, "x-auth-key": acc.userkey}
    for doc, expected in ((DOC_CANON, ["older copy"]), (DOC_ALIAS, ["newer copy", "another clipping"])):
        page = client.get(f"/api/v1/api/v1/clippings/{doc}?cursor=0", headers=headers).json()
        assert [i["text"] for i in page["items"]] == expected


def test_a_deletion_under_any_hash_of_a_book_hides_the_clipping(db, client, admin_user, library_book):
    user, _ = admin_user
    for doc in (DOC_CANON, DOC_ALIAS):
        db.add(KOSyncDocumentMap(tome_user_id=user.id, document=doc, book_id=library_book.id))
    db.add(_clip_row(user.id, DOC_CANON, "same-id", "still live here", 300, 1))
    db.add(_clip_row(user.id, DOC_ALIAS, "same-id", None, 100, 2, deleted=True))
    db.add(_clip_row(user.id, DOC_CANON, "kept-id", "kept", 100, 3))
    db.flush()
    text, per_book = _shown(client, library_book.id)
    assert "still live here" not in text and "kept" in text
    assert per_book == ["kept"]


# ── Tome's own status wins over the history ───────────────────────────────────

def _status(db, user_id, book_id):
    return db.query(UserBookStatus).filter_by(user_id=user_id, book_id=book_id).one()


def test_a_re_read_in_tome_is_kept(db, client, admin_user, library_book):
    """The export says finished in March 2025; Tome has the book as reading at
    10% (a re-read). Tome's row wins."""
    user, _ = admin_user
    db.add(UserBookStatus(user_id=user.id, book_id=library_book.id, status="reading", progress_pct=0.1))
    db.flush()
    report = _post(client, APPLY).json()
    row = _status(db, user.id, library_book.id)
    assert (row.status, row.progress_pct, row.finished_at) == ("reading", 0.1, None)
    line = {r["document"]: r for r in report["book_list"]}[DOC_LIB]
    assert line["status_change"] == "status kept: Tome has reading at 10%"
    assert report["statuses"]["counts"]["kept"] == 1
    assert "1 kept as Tome has them" in report["text"]


def test_a_shelved_book_is_kept(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(UserBookStatus(user_id=user.id, book_id=library_book.id, status="shelved", progress_pct=0.4))
    db.flush()
    report = _post(client, APPLY).json()
    row = _status(db, user.id, library_book.id)
    assert (row.status, row.progress_pct, row.finished_at) == ("shelved", 0.4, None)
    assert {r["document"]: r for r in report["book_list"]}[DOC_LIB]["status_change"] == \
        "status kept: Tome has shelved at 40%"


def test_a_want_to_read_book_takes_the_history(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(UserBookStatus(user_id=user.id, book_id=library_book.id, status="want_to_read"))
    db.flush()
    _post(client, APPLY)
    row = _status(db, user.id, library_book.id)
    assert (row.status, row.progress_pct, row.finished_at) == ("read", 1.0, datetime(2025, 3, 2, 12))


def test_a_read_book_without_a_finish_date_gets_the_files(db, client, admin_user, library_book):
    user, _ = admin_user
    db.add(UserBookStatus(user_id=user.id, book_id=library_book.id, status="read", progress_pct=1.0))
    db.flush()
    report = _post(client, APPLY).json()
    row = _status(db, user.id, library_book.id)
    assert (row.status, row.finished_at) == ("read", datetime(2025, 3, 2, 12))
    assert {r["document"]: r for r in report["book_list"]}[DOC_LIB]["status_change"] == \
        "finish date filled in: 2025-03-02"
    assert report["statuses"]["counts"]["finish_dated"] == 1


# ── finish dates ──────────────────────────────────────────────────────────────

def _finished_by_status_only(archive, progress_updated_at):
    """DOC_LIB finished by status alone: no status_at, no finished_date, no
    completed snapshot, no progress history; progress 1.0 at the given time."""
    doc = archive["data"]["documents"][0]
    doc["status_at"] = None
    doc["finished_date"] = None
    archive["data"]["stats_device_book"] = [r for r in archive["data"]["stats_device_book"] if r["document"] != DOC_LIB]
    archive["data"]["progress_log"] = []
    archive["data"]["progress"][0]["updated_at"] = progress_updated_at
    return archive


def test_a_finish_without_its_own_date_takes_the_positions(db, client, admin_user, library_book):
    user, _ = admin_user
    archive = _finished_by_status_only(build_archive(), _ts(2025, 3, 1))
    _post(client, APPLY, archive)
    assert _status(db, user.id, library_book.id).finished_at == datetime(2025, 3, 1)


def test_a_finish_with_no_date_at_all_says_it_is_dated_at_the_import(db, client, admin_user, library_book):
    user, _ = admin_user
    archive = _finished_by_status_only(build_archive(), 10**20)   # an unusable time
    report = _post(client, APPLY, archive).json()
    row = _status(db, user.id, library_book.id)
    assert row.status == "read" and row.finished_at.date() == datetime.utcnow().date()
    assert {r["document"]: r for r in report["book_list"]}[DOC_LIB]["status_change"] == \
        "marked finished; the file has no date for it, so it is dated at the import"


# ── values out of range ───────────────────────────────────────────────────────

def test_an_unusable_position_time_on_a_finish_by_position_is_not_a_500(db, client, admin_user, library_book):
    user, _ = admin_user
    archive = build_archive()
    archive["data"]["progress"][1].update(percentage=1.0, updated_at=10**20)   # Salt and Lantern, by position
    r = _post(client, APPLY, archive)
    assert r.status_code == 200, r.text
    salt = db.query(Book).filter(Book.title == "Salt and Lantern").one()
    row = _status(db, user.id, salt.id)
    assert row.status == "read"
    assert "dated at the import" in {x["document"]: x for x in r.json()["book_list"]}[DOC_NEW]["status_change"]


@pytest.mark.parametrize("at", [-10**13, 1, 10**20])
def test_progress_history_times_before_2000_or_unreadable_are_not_used(db, client, admin_user, library_book, at):
    user, _ = admin_user
    archive = build_archive()
    doc = archive["data"]["documents"][0]
    doc["status"] = None
    doc["finished_date"] = None
    archive["data"]["stats_device_book"] = []
    archive["data"]["progress"][0]["percentage"] = 0.5
    archive["data"]["progress_log"][1]["at"] = at
    r = _post(client, APPLY, archive)
    assert r.status_code == 200, r.text
    row = _status(db, user.id, library_book.id)
    assert (row.status, row.finished_at) == ("reading", None)    # never finished in 1970
    assert any("progress history rows have a time before 2000" in n for n in r.json()["notes"])


def test_a_payload_nested_too_deep_is_skipped_not_a_500(db, client, library_book):
    archive = build_archive()
    archive["data"]["stats_device_book"][1]["payload"] = "[" * 200000
    archive["data"]["stats_device_global"][0]["payload"] = "[" * 200000
    r = _post(client, APPLY, archive)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["book_pages"]["skip_reasons"][0]["reason"] == "totals are not readable JSON"
    assert report["devices"]["skip_reasons"][0]["reason"] == "totals are not readable JSON"


# ── not reader files ──────────────────────────────────────────────────────────

def test_tracker_imports_and_bare_statuses_are_not_books(db, client, admin_user, library_book):
    """CrossPoint Sync's own Goodreads import makes documents like
    ``goodreads.123`` with a status: not files on a reader."""
    user, _ = admin_user
    archive = build_archive()
    data = archive["data"]
    data["documents"] += [
        _document("goodreads.4242", "A Shelf Book", "Gil Tanner", status="finished", status_at=_ts(2024, 5, 1),
                  finished_date=_ts(2024, 5, 1)),
        _document("x" * 5000, "Very Long Id", "Nobody", status="finished"),
        _document("77777777777777777777777777777777", "Only A Status", "Ana Bell", status="reading"),
    ]
    data["document_aliases"].append(
        {"alias": "storygraph." + "a" * 40, "document": DOC_LIB, "created_at": 1, "merge_stats": 0})
    before_books = db.query(Book).count()
    report = _post(client, APPLY, archive).json()
    reasons = {r["reason"]: r for r in report["books"]["skip_reasons"]}
    assert reasons["not a reader file (tracker import)"]["count"] == 2
    assert reasons["no reading recorded for this file"]["count"] == 2   # the idle file and the bare status
    assert all(len(ex["document"]) <= 64 for r in reasons.values() for ex in r["examples"])
    assert db.query(Book).count() == before_books + 2                     # Salt and Lantern, Harbor only
    docs = {m.document for m in db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id)}
    assert all(len(d) == 32 for d in docs) and "storygraph." + "a" * 40 not in docs
    metas = {m.document for m in db.query(KOSyncDocumentMeta).filter_by(user_id=user.id)}
    assert all(len(d) == 32 for d in metas)
    assert "77777777777777777777777777777777" not in metas


# ── everything the import writes is in the report ─────────────────────────────

def test_the_report_counts_descriptions_earlier_sessions_and_device_names(db, client, admin_user, library_book):
    user, _ = admin_user
    # An earlier live session for DOC_NEW, waiting for its book; a device row without a name.
    earlier = ReadingSession(user_id=user.id, book_id=None, started_at=datetime(2025, 9, 1, 8),
                             ended_at=datetime(2025, 9, 1, 9), duration_seconds=1800, device="Xteink X4")
    db.add(earlier)
    db.flush()
    db.add(KOSyncSessionReceipt(user_id=user.id, device_id=DEVICE, session_uuid="9" * 32, document=DOC_NEW,
                                started_at=earlier.started_at, ended_at=earlier.ended_at, seconds=1800,
                                reading_session_id=earlier.id))
    db.add(KOSyncDeviceStats(user_id=user.id, device_id=DEVICE, device=None, snapshot="{}",
                             updated_at=datetime(2026, 1, 1)))
    db.flush()
    preview = _post(client, PREVIEW).json()
    assert preview["also_written"]["counts"] == {"descriptions": 3, "earlier_sessions_linked": 1, "device_names": 1}
    assert "Also written:" in preview["text"]
    report = _post(client, APPLY).json()
    assert report["also_written"]["counts"] == preview["also_written"]["counts"]
    salt = db.query(Book).filter(Book.title == "Salt and Lantern").one()
    assert db.get(ReadingSession, earlier.id).book_id == salt.id
    again = _post(client, PREVIEW).json()
    assert again["also_written"]["counts"] == {} and again["nothing_new"] is True


def test_a_preview_never_wakes_the_hardcover_sync(db, client, library_book, monkeypatch):
    from backend.services import hardcover_sync
    calls = []
    monkeypatch.setattr(hardcover_sync, "nudge", lambda: calls.append(1))
    _post(client, PREVIEW)
    assert calls == []
    _post(client, APPLY)
    assert calls == [1]
