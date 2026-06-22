"""Tests for the KOReader statistics.sqlite3 importer (stats-expansion Phase 2.1).

Covers the validated matcher rules (volume-aware, filename-exact, colon-in-title) and
the idempotent page-stat ingest. Synthetic data — no dependency on local personal DBs.
"""
from backend.services.ko_stats_import import (
    BookCandidate,
    match_book,
    parse_ko_title,
    import_batch,
)
from backend.models.ko_stats import PageStat, KoStatsBookMatch, StatsImport


# ── Title parsing ─────────────────────────────────────────────────────────────

def test_parse_volume_forms():
    assert parse_ko_title("Black Summoner: Volume 5") == ("black summoner", 5)
    assert parse_ko_title("That Time I Got Reincarnated as a Slime, Vol. 14")[1] == 14
    assert parse_ko_title("The Beginning After the End: Book 1: Early Years")[1] == 1
    assert parse_ko_title("Die Legende vom Tränenvogel 02 - Der träumende Krieger")[1] == 2
    # No volume -> None
    assert parse_ko_title("Dungeon Mauling")[1] is None


def test_colon_in_title_not_truncated():
    # 'Re:ZERO' must keep both words (no space after colon) — not collapse to 'Re'.
    base, _ = parse_ko_title("Re:ZERO -Starting Life in Another World- Vol. 28")
    assert base.startswith("re zero")


# ── Matcher ───────────────────────────────────────────────────────────────────

def _summoner(n: int) -> BookCandidate:
    return BookCandidate(id=100 + n, title="Black Summoner", author="Doufu Mayoi",
                         series="Black Summoner", series_index=float(n))


def test_volume_aware_does_not_collapse_series():
    cands = [_summoner(n) for n in range(1, 16)]
    # Each KOReader volume must map to its OWN book, not all to v1.
    r5 = match_book(cands, "Black Summoner: Volume 5", "Doufu Mayoi")
    r12 = match_book(cands, "Black Summoner: Volume 12", "Doufu Mayoi")
    assert r5.book_id == 105 and r5.status == "matched"
    assert r12.book_id == 112 and r12.status == "matched"
    assert r5.book_id != r12.book_id


def test_distinct_title_volume_matches_by_title():
    cands = [
        BookCandidate(1, "Dungeon Mauling", "Eric Ugland", "The Good Guys", 3.0),
        BookCandidate(2, "Heir Today, Pawn Tomorrow", "Eric Ugland", "The Good Guys", 2.0),
    ]
    r = match_book(cands, "Dungeon Mauling", "Eric Ugland")
    assert r.book_id == 1 and r.status == "matched"


def test_filename_exact_wins():
    cands = [BookCandidate(1, "Whatever", None, None, None)]
    idx = {"the_good_guys_-_vol._3.epub": 42}
    r = match_book(cands, "unrelated title", None,
                   filename="/mnt/us/books/The_Good_Guys_-_Vol._3.epub", path_index=idx)
    assert r.book_id == 42 and r.method == "filename" and r.confidence == 1.0


def test_junk_is_unmatched():
    cands = [BookCandidate(1, "Black Summoner", "x", "Black Summoner", 1.0)]
    r = match_book(cands, "T6otB1gNHQ9I9yFg089KuOD4wpJ0PMRkTC3mlT4nMV8", None)
    assert r.status == "unmatched" and r.book_id is None


def test_parsed_volume_weak_series_goes_to_review_not_dropped():
    # Volume parsed + exact (series,index) candidate exists, but series name barely matches
    # -> review, never silently unmatched.
    cands = [BookCandidate(1, "Re:ZERO", "Tappei", "Re:ZERO", 28.0)]
    r = match_book(cands, "Re:ZERO -Starting Life in Another World- Vol. 28", "Tappei")
    assert r.book_id == 1 and r.status in ("matched", "review")


# ── Import orchestration ──────────────────────────────────────────────────────

def test_import_idempotent_and_backfills(db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=1.0)
    payload = dict(
        device="Kindle",
        books=[{"ko_id": 7, "md5": "abc123", "title": "Black Summoner: Volume 1",
                "authors": "Doufu Mayoi"}],
        page_stats=[
            {"ko_id": 7, "page": 10, "start_time": 1700000000, "duration": 30, "total_pages": 200},
            {"ko_id": 7, "page": 11, "start_time": 1700000050, "duration": 45, "total_pages": 200},
        ],
    )
    r1 = import_batch(db, user, **payload)
    assert r1["matched"] == 1
    assert r1["page_rows_imported"] == 2
    assert db.query(PageStat).filter(PageStat.user_id == user.id).count() == 2

    # Re-running the exact same batch imports nothing new (idempotent).
    r2 = import_batch(db, user, **payload)
    assert r2["page_rows_imported"] == 0
    assert r2["page_rows_skipped"] == 2
    assert db.query(PageStat).filter(PageStat.user_id == user.id).count() == 2

    # Match cached + watermark advanced.
    m = db.query(KoStatsBookMatch).filter(KoStatsBookMatch.ko_md5 == "abc123").one()
    assert m.book_id == book.id and m.status == "matched"
    wm = db.query(StatsImport).filter(StatsImport.device == "Kindle").one()
    assert wm.last_start_time_synced == 1700000050


def test_import_never_writes_read_status(db, admin_user, make_book):
    """Status is user-curation: the import must never create/flip read/reading status,
    even for a book KOReader shows fully read."""
    from backend.models.user_book_status import UserBookStatus
    user, _ = admin_user
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=1.0)
    import_batch(
        db, user, device="Kindle",
        books=[{"ko_id": 1, "md5": "a", "title": "Black Summoner: Volume 1",
                "authors": "Doufu Mayoi", "pages": 200, "total_read_pages": 200}],
        page_stats=[{"ko_id": 1, "page": 199, "start_time": 1700000000, "duration": 30, "total_pages": 200}],
    )
    # Page data imported, but no status row was created.
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=book.id).count() == 1
    assert db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first() is None


def test_unmatched_book_parks_its_pages(db, admin_user, make_book):
    user, _ = admin_user
    make_book(title="Some Other Book", author="Nobody")
    r = import_batch(
        db, user, device="Kindle",
        books=[{"ko_id": 1, "md5": "zzz", "title": "Totally Unowned Title XYZ", "authors": "Ghost"}],
        page_stats=[{"ko_id": 1, "page": 1, "start_time": 1700000000, "duration": 10, "total_pages": 100}],
    )
    assert r["unmatched"] == 1
    assert r["page_rows_imported"] == 0          # parked, not attributed to a wrong book
    m = db.query(KoStatsBookMatch).filter(KoStatsBookMatch.ko_md5 == "zzz").one()
    assert m.book_id is None and m.status == "unmatched"
