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


def test_duplicate_md5_in_batch_no_crash(db, admin_user, make_book):
    """KOReader re-downloads create multiple book rows sharing one md5. With the
    server's autoflush=False session this must not violate UNIQUE(user, md5)."""
    user, _ = admin_user
    book = make_book(title="Black Summoner", author="x", series="Black Summoner", series_index=1.0)
    db.autoflush = False  # mirror backend SessionLocal (the POC masked this with autoflush=True)
    try:
        res = import_batch(
            db, user, device="Kindle",
            books=[
                {"ko_id": 1, "md5": "samehash", "title": "Black Summoner: Volume 1", "authors": "x"},
                {"ko_id": 2, "md5": "samehash", "title": "Black Summoner: Volume 1", "authors": "x"},
            ],
            page_stats=[
                {"ko_id": 1, "page": 1, "start_time": 1700000000, "duration": 10, "total_pages": 100},
                {"ko_id": 2, "page": 2, "start_time": 1700000100, "duration": 10, "total_pages": 100},
            ],
        )
    finally:
        db.autoflush = True
    assert db.query(KoStatsBookMatch).filter_by(user_id=user.id, ko_md5="samehash").count() == 1
    assert res["page_rows_imported"] == 2
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=book.id).count() == 2


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


# ── Layer-3 volume guard (issue #152) ─────────────────────────────────────────
# A volume Tome doesn't own must never strong-match a sibling volume: numbered
# titles differ by one digit (ratio 0.95+) and same-titled series volumes are
# identical after de-subtitling (ratio 1.0), so without the guard a whole
# book's reading history imports onto the wrong book.

def _dcc(n: int, titled: bool = True) -> BookCandidate:
    """Dungeon Crawler Carl shaped candidate: numbered title or clean title."""
    title = f"Dungeon Crawler Carl {n}" if titled else "Dungeon Crawler Carl"
    return BookCandidate(id=200 + n, title=title, author="Matt Dinniman",
                         series="Dungeon Crawler Carl", series_index=float(n))


def test_missing_volume_does_not_match_numbered_sibling():
    # The reporter's exact shape: vol 2 absent from Tome, siblings 1/3/5/6 present.
    cands = [_dcc(n) for n in (1, 3, 5, 6)]
    for ko_title in ("Dungeon Crawler Carl 2", "Dungeon Crawler Carl Book 2",
                     "Dungeon Crawler Carl, Book 2", "Dungeon Crawler Carl T2"):
        r = match_book(cands, ko_title, "Matt Dinniman")
        assert r.book_id is None, f"{ko_title!r} wrongly matched book {r.book_id}"
        assert r.status == "unmatched"


def test_missing_volume_does_not_match_same_titled_sibling():
    # Same-titled series volumes ("Black Summoner" x15): the de-subtitled KO
    # title is identical to every sibling's title (ratio 1.0). Without the
    # guard, a missing volume matched the first sibling in the list.
    cands = [_summoner(n) for n in (1, 3, 5)]
    r = match_book(cands, "Black Summoner: Volume 2", "Doufu Mayoi")
    assert r.book_id is None and r.status == "unmatched"


def test_present_volume_still_matches_exactly():
    # Control: when the volume IS in the library, both title shapes resolve to it.
    cands = [_dcc(n) for n in (1, 2, 3, 5)]
    r = match_book(cands, "Dungeon Crawler Carl 2", "Matt Dinniman")
    assert r.book_id == 202 and r.status == "matched"
    cands2 = [_summoner(n) for n in range(1, 16)]
    r2 = match_book(cands2, "Black Summoner: Volume 2", "Doufu Mayoi")
    assert r2.book_id == 102 and r2.status == "matched"


def test_distinct_title_with_volume_suffix_matches_by_title():
    # Publisher long-form KO title, clean distinct titles in Tome: the candidate
    # at the SAME index stays eligible and wins on title ratio.
    cands = [
        BookCandidate(1, "Dungeon Crawler Carl", "Matt Dinniman", "Dungeon Crawler Carl", 1.0),
        BookCandidate(5, "The Butcher's Masquerade", "Matt Dinniman", "Dungeon Crawler Carl", 5.0),
    ]
    r = match_book(cands, "The Butcher's Masquerade: Dungeon Crawler Carl Book 5", "Matt Dinniman")
    assert r.book_id == 5 and r.status == "matched"


def test_half_volume_resolves_exactly():
    # "Vol. 2.5" must parse as 2.5 (not truncate to 2) and hit the 2.5 book;
    # "Vol. 2" with only 1 and 2.5 in the library must park, not land on either.
    cands = [
        BookCandidate(1, "Black Summoner", "Doufu Mayoi", "Black Summoner", 1.0),
        BookCandidate(25, "Black Summoner", "Doufu Mayoi", "Black Summoner", 2.5),
    ]
    r = match_book(cands, "Black Summoner: Volume 2.5", "Doufu Mayoi")
    assert r.book_id == 25 and r.status == "matched"
    r2 = match_book(cands, "Black Summoner: Volume 2", "Doufu Mayoi")
    assert r2.book_id is None and r2.status == "unmatched"


def test_french_tome_abbreviation_parses():
    assert parse_ko_title("La Horde du Contrevent T2")[1] == 2
    # ... and a missing T-volume parks instead of matching a sibling.
    cands = [BookCandidate(1, "La Horde du Contrevent T1", "Alain Damasio",
                           "La Horde du Contrevent", 1.0)]
    r = match_book(cands, "La Horde du Contrevent T2", "Alain Damasio")
    assert r.book_id is None and r.status == "unmatched"


def test_title_number_standalone_still_matches():
    # "Fahrenheit 451" parses as volume 451; a standalone candidate has no
    # volume signal and stays eligible.
    cands = [BookCandidate(1, "Fahrenheit 451", "Ray Bradbury", None, None)]
    r = match_book(cands, "Fahrenheit 451", "Ray Bradbury")
    assert r.book_id == 1 and r.status == "matched"
    # Even shelved as index 1 of its own series, the title's own 451 keeps it
    # eligible (either volume signal may agree).
    cands2 = [BookCandidate(1, "Fahrenheit 451", "Ray Bradbury", "Fahrenheit 451", 1.0)]
    r2 = match_book(cands2, "Fahrenheit 451", "Ray Bradbury")
    assert r2.book_id == 1 and r2.status == "matched"


def test_import_batch_parks_missing_volume(db, admin_user, make_book):
    # End-to-end: the ghost scenario from issue #152 imports zero rows.
    user, _ = admin_user
    for n in (1, 3, 5):
        make_book(title=f"Dungeon Crawler Carl {n}", author="Matt Dinniman",
                  series="Dungeon Crawler Carl", series_index=float(n))
    r = import_batch(
        db, user, device="Kindle",
        books=[{"ko_id": 1, "md5": "vol2md5", "title": "Dungeon Crawler Carl 2",
                "authors": "Matt Dinniman"}],
        page_stats=[{"ko_id": 1, "page": p, "start_time": 1700000000 + p * 60,
                     "duration": 50, "total_pages": 811} for p in range(1, 30)],
    )
    assert r["unmatched"] == 1 and r["page_rows_imported"] == 0
    assert db.query(PageStat).filter(PageStat.user_id == user.id).count() == 0


# ── Startup repair of pre-guard wrong matches (issue #152) ────────────────────

from backend.services.ko_stats_import import repair_fuzzy_matches  # noqa: E402
from backend.models.notification import Notification  # noqa: E402


def _seed_ghost(db, user, make_book, *, md5="ghostmd5"):
    """Library 1/3/5, a wrong fuzzy match (KO vol 2 -> book 5) and its imported
    ghost pages + advanced watermark. Returns the wrongly-credited book."""
    books = {n: make_book(title=f"Dungeon Crawler Carl {n}", author="Matt Dinniman",
                          series="Dungeon Crawler Carl", series_index=float(n))
             for n in (1, 3, 5)}
    ghost_target = books[5]
    db.add(KoStatsBookMatch(
        user_id=user.id, ko_md5=md5,
        ko_title="Dungeon Crawler Carl 2", ko_authors="Matt Dinniman",
        book_id=ghost_target.id, confidence=0.9545, method="fuzzy", status="matched",
    ))
    for p in range(1, 30):
        db.add(PageStat(user_id=user.id, book_id=ghost_target.id, page=p,
                        total_pages=811, start_time=1700000000 + p * 60,
                        duration_seconds=50, device="Kindle"))
    db.add(StatsImport(user_id=user.id, device="Kindle",
                       last_start_time_synced=1700010000, rows_imported=29))
    db.flush()
    return ghost_target


def test_repair_reverts_wrong_match_and_deletes_ghost_pages(db, admin_user, make_book):
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)

    result = repair_fuzzy_matches(db)

    assert result["changed"] == 1
    assert result["pages_deleted"] == 29
    m = db.query(KoStatsBookMatch).filter_by(ko_md5="ghostmd5").one()
    assert m.book_id is None and m.status == "unmatched"
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 0
    # Watermark reset so the device re-uploads history under the fixed rules.
    wm = db.query(StatsImport).filter_by(user_id=user.id, device="Kindle").one()
    assert wm.last_start_time_synced == 0
    notes = db.query(Notification).filter_by(user_id=user.id, kind="stats_repair").all()
    assert len(notes) == 1 and "Removed misattributed" in notes[0].title


def test_repair_keeps_mixed_book_and_notifies(db, admin_user, make_book):
    # The wrongly-credited book ALSO has a legitimate import source: rows can't
    # be told apart, so nothing is deleted and the user is pointed at the
    # manual "Clear imported history" action.
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)
    db.add(KoStatsBookMatch(
        user_id=user.id, ko_md5="legitmd5",
        ko_title="Dungeon Crawler Carl 5", ko_authors="Matt Dinniman",
        book_id=ghost.id, confidence=1.0, method="ko_hash", status="matched",
    ))
    db.flush()

    result = repair_fuzzy_matches(db)

    assert result["changed"] == 1 and result["kept_mixed"] == 1
    assert result["pages_deleted"] == 0
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 29
    # No rebuild needed -> watermark untouched.
    wm = db.query(StatsImport).filter_by(user_id=user.id, device="Kindle").one()
    assert wm.last_start_time_synced == 1700010000
    notes = db.query(Notification).filter_by(user_id=user.id, kind="stats_repair").all()
    assert len(notes) == 1 and "may include another book" in notes[0].title


def test_repair_is_idempotent(db, admin_user, make_book):
    user, _ = admin_user
    _seed_ghost(db, user, make_book)
    first = repair_fuzzy_matches(db)
    second = repair_fuzzy_matches(db)
    assert first["changed"] == 1
    assert second["changed"] == 0 and second["pages_deleted"] == 0
    assert db.query(Notification).filter_by(user_id=user.id, kind="stats_repair").count() == 1


def test_repair_skips_confirmed_rows(db, admin_user, make_book):
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)
    db.query(KoStatsBookMatch).filter_by(ko_md5="ghostmd5").update({"confirmed": True})
    db.flush()
    result = repair_fuzzy_matches(db)
    assert result["checked"] == 0 and result["changed"] == 0
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 29


def test_repair_rematches_via_hash_when_available(db, admin_user, make_book):
    # If Tome has since recorded the device file's partial-MD5 (the book was
    # added/served), the repair resolves deterministically to the right book.
    from backend.models.ko_stats import KoHash
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)
    real_vol2 = make_book(title="Dungeon Crawler Carl 2", author="Matt Dinniman",
                          series="Dungeon Crawler Carl", series_index=2.0)
    db.add(KoHash(book_id=real_vol2.id, ko_partial_md5="ghostmd5", kind="raw"))
    db.flush()

    result = repair_fuzzy_matches(db)

    assert result["changed"] == 1
    m = db.query(KoStatsBookMatch).filter_by(ko_md5="ghostmd5").one()
    assert m.book_id == real_vol2.id and m.method == "ko_hash" and m.status == "matched"
    # Ghost pages on the old book still cleaned up; re-sync refills the right book.
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 0


def test_repair_leaves_valid_matches_alone(db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=1.0)
    db.add(KoStatsBookMatch(
        user_id=user.id, ko_md5="finemd5",
        ko_title="Black Summoner: Volume 1", ko_authors="Doufu Mayoi",
        book_id=book.id, confidence=1.0, method="fuzzy", status="matched",
    ))
    db.add(PageStat(user_id=user.id, book_id=book.id, page=1, total_pages=200,
                    start_time=1700000000, duration_seconds=30, device="Kindle"))
    db.add(StatsImport(user_id=user.id, device="Kindle",
                       last_start_time_synced=1700000000, rows_imported=1))
    db.flush()

    result = repair_fuzzy_matches(db)

    assert result["changed"] == 0
    m = db.query(KoStatsBookMatch).filter_by(ko_md5="finemd5").one()
    assert m.book_id == book.id and m.status == "matched"
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=book.id).count() == 1
    wm = db.query(StatsImport).filter_by(user_id=user.id, device="Kindle").one()
    assert wm.last_start_time_synced == 1700000000
    assert db.query(Notification).filter_by(user_id=user.id).count() == 0


def test_repair_two_bad_rows_same_book_single_cleanup(db, admin_user, make_book):
    # Two wrong matches piled onto the same book: one cleanup, one notification.
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)
    db.add(KoStatsBookMatch(
        user_id=user.id, ko_md5="ghostmd5b",
        ko_title="Dungeon Crawler Carl 4", ko_authors="Matt Dinniman",
        book_id=ghost.id, confidence=0.9545, method="fuzzy", status="matched",
    ))
    db.flush()

    result = repair_fuzzy_matches(db)

    assert result["changed"] == 2
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 0
    assert db.query(Notification).filter_by(user_id=user.id, kind="stats_repair").count() == 1


def test_repair_scopes_cleanup_per_user(db, admin_user, make_book):
    # Another user's genuine history on the same book must survive user A's cleanup.
    from backend.models.user import User as UserModel
    from backend.core.security import hash_password
    user, _ = admin_user
    ghost = _seed_ghost(db, user, make_book)
    other = UserModel(username="reader2", email="r2@example.com",
                      hashed_password=hash_password("pw12345678"), is_active=True)
    db.add(other)
    db.flush()
    db.add(KoStatsBookMatch(
        user_id=other.id, ko_md5="othermd5",
        ko_title="Dungeon Crawler Carl 5", ko_authors="Matt Dinniman",
        book_id=ghost.id, confidence=1.0, method="ko_hash", status="matched",
    ))
    db.add(PageStat(user_id=other.id, book_id=ghost.id, page=1, total_pages=811,
                    start_time=1700000000, duration_seconds=30, device="Boox"))
    db.add(StatsImport(user_id=other.id, device="Boox",
                       last_start_time_synced=1700000000, rows_imported=1))
    db.flush()

    repair_fuzzy_matches(db)

    # User A cleaned up; user B untouched (pages, watermark, no notification).
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=ghost.id).count() == 0
    assert db.query(PageStat).filter_by(user_id=other.id, book_id=ghost.id).count() == 1
    wm_b = db.query(StatsImport).filter_by(user_id=other.id, device="Boox").one()
    assert wm_b.last_start_time_synced == 1700000000
    assert db.query(Notification).filter_by(user_id=other.id).count() == 0


# ── Clear-imported-history endpoint ───────────────────────────────────────────

def test_clear_imported_history_endpoint(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Dungeon Crawler Carl 5", author="Matt Dinniman",
                     series="Dungeon Crawler Carl", series_index=5.0)
    from backend.models.user import User as UserModel
    from backend.core.security import hash_password
    other = UserModel(username="someoneelse", email="e@example.com",
                      hashed_password=hash_password("pw12345678"), is_active=True)
    db.add(other)
    db.flush()
    for p in (1, 2):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=p, total_pages=811,
                        start_time=1700000000 + p, duration_seconds=30, device="Kindle"))
    db.add(PageStat(user_id=other.id, book_id=book.id, page=1, total_pages=811,
                    start_time=1700000000, duration_seconds=30, device="Kindle"))
    db.add(KoStatsBookMatch(user_id=user.id, ko_md5="m1", ko_title="Dungeon Crawler Carl 2",
                            book_id=book.id, confidence=0.95, method="fuzzy", status="matched"))
    db.add(KoStatsBookMatch(user_id=user.id, ko_md5="m2", ko_title="Dungeon Crawler Carl 5",
                            book_id=book.id, confidence=1.0, method="ko_hash", status="matched"))
    db.flush()

    resp = client.delete(f"/api/books/{book.id}/imported-history")
    assert resp.status_code == 200
    assert resp.json() == {"pages_deleted": 2, "matches_reset": 1}

    # Own rows gone, the other user's row untouched.
    assert db.query(PageStat).filter_by(user_id=user.id, book_id=book.id).count() == 0
    assert db.query(PageStat).filter_by(user_id=other.id, book_id=book.id).count() == 1
    # Fuzzy match reset for re-decision; deterministic hash match left alone.
    m1 = db.query(KoStatsBookMatch).filter_by(ko_md5="m1").one()
    assert m1.book_id is None and m1.status == "unmatched" and m1.method == "none"
    m2 = db.query(KoStatsBookMatch).filter_by(ko_md5="m2").one()
    assert m2.book_id == book.id and m2.status == "matched"


def test_clear_imported_history_unknown_book_404(client):
    resp = client.delete("/api/books/999999/imported-history")
    assert resp.status_code == 404
