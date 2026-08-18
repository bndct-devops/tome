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


# ── Gap-clustered session counts (replaced the one-per-(book,day) approximation) ──

def _seed_stats(db, user_id, book_id, rows):
    from backend.models.ko_stats import PageStat
    for start, dur in rows:
        db.add(PageStat(user_id=user_id, book_id=book_id, page=1, total_pages=100,
                        start_time=start, duration_seconds=dur, device="Kindle"))
    db.commit()


def test_two_sittings_same_day_are_two_sessions(db, admin_user, make_book):
    from backend.services.reconciled_reading import totals, covered_book_ids
    user, _ = admin_user
    book = make_book(title="Cluster Book", author="A")
    base = 1_720_000_000  # mid-day epoch
    # morning sitting: 3 pages close together; evening sitting 4h later
    _seed_stats(db, user.id, book.id,
                [(base, 60), (base + 70, 60), (base + 140, 60),
                 (base + 4 * 3600, 60), (base + 4 * 3600 + 90, 60)])
    covered = covered_book_ids(db, user.id)
    secs, sessions, pages = totals(db, user.id, "+0 hours", covered, None, None)
    assert sessions == 2          # the old approximation reported 1
    assert secs == 300 and pages == 5


def test_midnight_crossing_is_one_session_on_start_day(db, admin_user, make_book):
    from backend.services.reconciled_reading import daily_map, covered_book_ids
    from datetime import datetime, timezone
    user, _ = admin_user
    book = make_book(title="Midnight Book", author="A")
    # 23:50 UTC .. 00:20 UTC — continuous reading across the day boundary
    start = int(datetime(2026, 3, 10, 23, 50, tzinfo=timezone.utc).timestamp())
    _seed_stats(db, user.id, book.id,
                [(start + i * 300, 240) for i in range(7)])  # 35 min span
    covered = covered_book_ids(db, user.id)
    dm = daily_map(db, user.id, "+0 hours", covered, None, None)
    total_sessions = sum(v[1] for v in dm.values())
    assert total_sessions == 1                       # old: 2 (one per day touched)
    assert dm["2026-03-10"][1] == 1                  # attributed to the start day
    assert dm.get("2026-03-11", (0, 0, 0))[1] == 0   # not double-counted
    # seconds still land on the day the pages were read
    assert dm["2026-03-11"][0] > 0


def test_noise_flip_not_counted_as_session(db, admin_user, make_book):
    from backend.services.reconciled_reading import totals, covered_book_ids
    user, _ = admin_user
    book = make_book(title="Flip Book", author="A")
    base = 1_720_100_000
    _seed_stats(db, user.id, book.id,
                [(base, 3),                    # 3s flip — below MIN_SESSION_SECONDS
                 (base + 7200, 120), (base + 7300, 120)])  # a real sitting later
    covered = covered_book_ids(db, user.id)
    _, sessions, _ = totals(db, user.id, "+0 hours", covered, None, None)
    assert sessions == 1


def test_per_book_session_counts_are_clustered(db, admin_user, make_book):
    from backend.services.reconciled_reading import book_seconds, covered_book_ids
    user, _ = admin_user
    book = make_book(title="Per Book Cluster", author="A")
    base = 1_720_200_000
    # three sittings, two on the same day — old distinct-day count said 2
    _seed_stats(db, user.id, book.id,
                [(base, 60), (base + 3 * 3600, 60), (base + 30 * 3600, 60)])
    covered = covered_book_ids(db, user.id)
    bs = book_seconds(db, user.id, "+0 hours", covered, None, None)
    assert bs[book.id][1] == 3


def test_session_timeline_ribbon_reconciled(client, db, admin_user, make_book):
    """The Habits session-timeline ribbon draws imported sittings and drops the
    superseded live device sessions that describe the same reading; web/manual
    sessions on covered books stay."""
    user, _ = admin_user
    covered = make_book(title="Covered Book")
    webonly = make_book(title="Web Book")
    # Imported sitting on the covered book + a live device session (same reading)
    _add_pagestats(db, user, covered, [120, 80], day=(2026, 1, 10))
    db.add(ReadingSession(user_id=user.id, book_id=covered.id,
                          started_at=datetime(2026, 1, 10, 12, 0),
                          ended_at=datetime(2026, 1, 10, 12, 30),
                          duration_seconds=1800, pages_turned=20, device="Kindle"))
    # Manual session on the covered book — additive, stays drawn
    db.add(ReadingSession(user_id=user.id, book_id=covered.id,
                          started_at=datetime(2026, 1, 11, 20, 0),
                          ended_at=datetime(2026, 1, 11, 20, 30),
                          duration_seconds=1800, pages_turned=20, device="manual"))
    # Session-only book — unaffected
    db.add(ReadingSession(user_id=user.id, book_id=webonly.id,
                          started_at=datetime(2026, 1, 12, 9, 0),
                          ended_at=datetime(2026, 1, 12, 9, 30),
                          duration_seconds=1800, pages_turned=20, device="web"))
    db.flush()

    tl = client.get("/api/stats?days=0").json()["session_timeline"]
    ids = [e["id"] for e in tl]
    # newest first: web book, manual, imported cluster
    assert [str(i).startswith("ps-") for i in ids] == [False, False, True]
    imported = tl[2]
    assert imported["duration_seconds"] == 200
    assert imported["title"] == "Covered Book"
    assert imported["started_at"] < imported["ended_at"]
    # the superseded 1800s device session is not drawn
    assert not any(e["duration_seconds"] == 1800 and e["title"] == "Covered Book"
                   and not str(e["id"]).startswith("ps-") and e["started_at"].startswith("2026-01-10")
                   for e in tl)


# ── Per-sitting supersession (issue #181) ────────────────────────────────────────

def _live(db, user, book, start, secs, pages=10, device="KOReader"):
    """A plugin-recorded device session, [start, start+secs]."""
    from datetime import timedelta
    row = ReadingSession(user_id=user.id, book_id=book.id, started_at=start,
                         ended_at=start + timedelta(seconds=secs),
                         duration_seconds=secs, pages_turned=pages, device=device)
    db.add(row)
    db.flush()
    return row


def _pagestats_at(db, user, book, start_epoch, rows, device="KOReader", page0=1):
    for i, secs in enumerate(rows):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=page0 + i, total_pages=300,
                        start_time=start_epoch + i * 60, duration_seconds=secs, device=device))


def test_second_device_live_sessions_survive_history_import(client, db, admin_user, make_book):
    """Issue #181: a week of phone reading (live sessions only) must not vanish
    when a Kindle later syncs its own reading history for the same book. Both
    devices report the literal device name "KOReader" (the plugin's fallback),
    so the rule can't lean on device identity — supersession is per sitting:
    only the live sessions that imported page-stats overlap in time drop out.
    """
    user, _ = admin_user
    book = make_book(title="Una corte de rosas y espinas")

    # Phone: 5 evenings, one 3600s live session each, no history sync at all.
    phone = [
        _live(db, user, book, datetime(2026, 8, 10 + i, 21, 0), 3600, pages=40)
        for i in range(5)
    ]
    # Kindle, Aug 16: two sittings — each recorded BOTH as a live session and as
    # imported page-stats (the same reading, described twice).
    k1_start, k2_start = datetime(2026, 8, 16, 12, 10), datetime(2026, 8, 16, 22, 50)
    kindle_live = [
        _live(db, user, book, k1_start, 3060, pages=30),   # 51m
        _live(db, user, book, k2_start, 3780, pages=38),   # 1h3m
    ]
    _pagestats_at(db, user, book, _epoch(2026, 8, 16, 12) + 600, [100] * 30)            # 3000s
    _pagestats_at(db, user, book, _epoch(2026, 8, 16, 22) + 3000, [100] * 37, page0=40)  # 3700s
    db.flush()

    # Dashboard totals: phone hours + Kindle page-stats, Kindle live sessions dropped.
    h = client.get("/api/stats?days=0").json()["headline"]
    assert h["total_reading_seconds"] == 5 * 3600 + 3000 + 3700
    assert h["pages_turned"] == 5 * 40 + 30 + 37

    # Per-book block: same total, first read is the phone's first evening,
    # sessions = 5 phone + 2 imported clusters.
    b = client.get(f"/api/books/{book.id}/reading-stats").json()["own"]
    assert b["total_seconds"] == 5 * 3600 + 3000 + 3700
    assert b["sessions"] == 7
    assert b["first_read"].startswith("2026-08-10")
    assert b["last_read"].startswith("2026-08-16T2")
    days = {d["date"]: d["seconds"] for d in b["session_timeline"]}
    assert days["2026-08-10"] == 3600 and days["2026-08-16"] == 6700
    # "Where you read": one merged KOReader row, not a page-stat row plus a session row.
    src = {r["device"]: r for r in b["by_source"]}
    assert set(src) == {"KOReader"}
    assert src["KOReader"]["seconds"] == 5 * 3600 + 3000 + 3700

    # Session log: phone rows counted, Kindle live rows labelled not counted.
    rows = client.get("/api/stats/sessions?limit=50").json()["sessions"]
    counted = {r["id"]: r["counted"] for r in rows if r["kind"] == "session"}
    assert all(counted[s.id] for s in phone)
    assert not any(counted[s.id] for s in kindle_live)
    assert sum(1 for r in rows if r["kind"] == "imported") == 2

    # Habits ribbon: phone sittings drawn, Kindle live ones replaced by clusters.
    tl = client.get("/api/stats?days=0").json()["session_timeline"]
    live_ids = {e["id"] for e in tl if not str(e["id"]).startswith("ps-")}
    assert {s.id for s in phone} <= live_ids
    assert not ({s.id for s in kindle_live} & live_ids)


def test_live_session_counts_until_its_history_arrives(client, db, admin_user, make_book):
    """Same device, ordinary flow: the live session posts at close, the page-stats
    for it arrive on a later launch. Before: it counts. After: it is superseded
    and the page-stats carry the sitting — totals never double and never dip."""
    user, _ = admin_user
    book = make_book(title="Kindle Book")
    # older synced sitting: page-stats + its live session
    _pagestats_at(db, user, book, _epoch(2026, 8, 1, 20), [120] * 10)          # 1200s
    _live(db, user, book, datetime(2026, 8, 1, 20, 0), 1200, pages=10)
    # tonight's sitting: live only so far
    tonight = _live(db, user, book, datetime(2026, 8, 2, 20, 0), 1800, pages=15)
    db.flush()
    assert client.get("/api/stats?days=0").json()["headline"]["total_reading_seconds"] == 3000
    row = next(r for r in client.get("/api/stats/sessions").json()["sessions"] if r["id"] == tonight.id)
    assert row["counted"] is True

    # history sync catches up
    _pagestats_at(db, user, book, _epoch(2026, 8, 2, 20), [120] * 15, page0=11)  # 1800s
    db.flush()
    assert client.get("/api/stats?days=0").json()["headline"]["total_reading_seconds"] == 3000
    row = next(r for r in client.get("/api/stats/sessions").json()["sessions"] if r["id"] == tonight.id)
    assert row["counted"] is False


def test_manual_session_never_superseded_even_when_overlapping(client, db, admin_user, make_book):
    """A hand-logged session on a device-synced book is additive even if its
    window overlaps page-stats (KOReader can't describe a paper session)."""
    user, _ = admin_user
    book = make_book(title="Paper and Kindle")
    _pagestats_at(db, user, book, _epoch(2026, 8, 1, 20), [120] * 10)   # 1200s
    _live(db, user, book, datetime(2026, 8, 1, 20, 5), 900, device="manual")
    db.flush()
    assert client.get("/api/stats?days=0").json()["headline"]["total_reading_seconds"] == 2100
