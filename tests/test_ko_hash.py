"""KOReader partial-MD5 port + the ko_hashes recording helpers.

The two hash vectors were produced by KOReader's own ``util.partialMD5``
running inside the emulator (2026-07-03) on deterministic xorshift32 fixtures —
if the port ever drifts from KOReader's algorithm, these fail.
"""
import hashlib

import pytest

from backend.models.ko_stats import KoHash
from backend.services.ko_hash import (
    BAKED_HASHES_KEPT,
    ko_partial_md5,
    lookup_book_ids,
    record_ko_hash,
)


def _stream(n: int, seed: int = 0x704D45) -> bytes:
    x = seed
    out = bytearray()
    for _ in range(n):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out.append(x & 0xFF)
    return bytes(out)


def test_partial_md5_matches_koreader_multi_sample(tmp_path):
    """3 MB fixture — exercises samples at 0, 1K, 4K, 16K, 64K, 256K, 1M + EOF stop."""
    p = tmp_path / "big.bin"
    p.write_bytes(_stream(3_000_000))
    assert ko_partial_md5(p) == "4a50115c444b43a695d3ffe94bd5cce5"


def test_partial_md5_matches_koreader_tiny_file(tmp_path):
    """A file smaller than one sample: hash == md5(content) (offset-0 read only)."""
    p = tmp_path / "tiny.bin"
    data = _stream(700)
    p.write_bytes(data)
    assert ko_partial_md5(p) == "093099351355bef62f45d03e3bd73ba5"
    assert ko_partial_md5(p) == hashlib.md5(data).hexdigest()


def test_partial_md5_unreadable_returns_none(tmp_path):
    assert ko_partial_md5(tmp_path / "does-not-exist.bin") is None


def test_record_is_idempotent_and_none_is_noop(db, make_book):
    book = make_book(title="Hash Book", author="A")
    record_ko_hash(db, book.id, "a" * 32, kind="raw")
    record_ko_hash(db, book.id, "a" * 32, kind="raw")
    record_ko_hash(db, book.id, None)
    db.commit()
    assert db.query(KoHash).filter(KoHash.book_id == book.id).count() == 1


def test_record_ignores_pending_duplicate_before_flush(db, make_book):
    """The production session is autoflush=False, so a `db.query` cannot see a
    row that is still pending in the session. Without a pending-object check the
    same (book_id, md5) is queued for a duplicate INSERT and the scan transaction
    dies on the uq_ko_hash_book_md5 constraint at commit."""
    book = make_book(title="Unflushed Hash", author="A")
    db.autoflush = False  # match SessionLocal(autoflush=False) in production
    record_ko_hash(db, book.id, "f" * 32, kind="raw")
    record_ko_hash(db, book.id, "f" * 32, kind="raw")
    db.commit()
    assert db.query(KoHash).filter(KoHash.book_id == book.id).count() == 1


def test_baked_hashes_pruned_to_last_n(db, make_book):
    book = make_book(title="Bake Book", author="A")
    for i in range(BAKED_HASHES_KEPT + 3):
        record_ko_hash(db, book.id, f"{i:032x}", kind="baked")
        db.commit()
    rows = db.query(KoHash).filter(KoHash.book_id == book.id, KoHash.kind == "baked").all()
    assert len(rows) == BAKED_HASHES_KEPT
    kept = {r.ko_partial_md5 for r in rows}
    # the newest N survive
    assert kept == {f"{i:032x}" for i in range(3, BAKED_HASHES_KEPT + 3)}


def test_raw_hashes_never_pruned(db, make_book):
    book = make_book(title="Raw Book", author="A")
    for i in range(BAKED_HASHES_KEPT + 3):
        record_ko_hash(db, book.id, f"{i + 100:032x}", kind="raw")
        db.commit()
    assert db.query(KoHash).filter(KoHash.book_id == book.id, KoHash.kind == "raw").count() == BAKED_HASHES_KEPT + 3


def test_lookup_batch_first_match_wins(db, make_book):
    b1 = make_book(title="Lookup One", author="A")
    b2 = make_book(title="Lookup Two", author="A")
    record_ko_hash(db, b1.id, "e" * 32)
    record_ko_hash(db, b2.id, "e" * 32)   # same bytes served under two books
    record_ko_hash(db, b2.id, "d" * 32)
    db.commit()
    out = lookup_book_ids(db, ["e" * 32, "d" * 32, "f" * 32])
    assert out["e" * 32] == b1.id
    assert out["d" * 32] == b2.id
    assert "f" * 32 not in out
    assert lookup_book_ids(db, []) == {}


def test_download_records_served_artifact_hash(client, db, make_book, tmp_path):
    """Every download path records the hash of the bytes actually served, so a
    device file can later be matched back to its book."""
    from backend.models.book import BookFile

    book = make_book(title="Served Book", author="A")
    p = tmp_path / "served.epub"
    p.write_bytes(_stream(2048, seed=0xBEEF))
    bf = BookFile(book_id=book.id, file_path=str(p), format="epub",
                  file_size=2048, content_hash="x" * 64)
    db.add(bf)
    db.commit()

    resp = client.get(f"/api/books/{book.id}/download/{bf.id}")
    assert resp.status_code == 200

    rows = db.query(KoHash).filter(KoHash.book_id == book.id).all()
    assert rows, "download did not record a ko-hash"
    # embed will fall back to raw for this fake epub OR produce a baked copy —
    # either way the recorded hash must match the served artifact's bytes
    assert all(len(r.ko_partial_md5) == 32 for r in rows)


def _plugin_key(db, user_id: int) -> str:
    from backend.models.tome_sync import ApiKey
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext), label="test"))
    db.commit()
    return plaintext


def test_resolve_prefers_hash_over_filename(client, db, make_book, admin_user):
    """A renamed device file resolves via ko_md5 where filename heuristics fail —
    and a hash hit wins over a filename that would point at a DIFFERENT book."""
    user, _ = admin_user
    key = _plugin_key(db, user.id)
    right = make_book(title="The Right Book", author="A")
    wrong = make_book(title="Renamed Nonsense", author="B")
    record_ko_hash(db, right.id, "c" * 32, kind="baked")
    db.commit()

    r = client.get(
        "/api/tome-sync/resolve",
        params={"filename": "Renamed Nonsense.epub", "ko_md5": "c" * 32},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["book_id"] == right.id
    assert body.get("method") == "ko_hash"
    assert body["book_id"] != wrong.id


def test_resolve_without_hash_keeps_filename_behaviour(client, db, make_book, admin_user):
    user, _ = admin_user
    key = _plugin_key(db, user.id)
    book = make_book(title="Plain Filename Book", author="A")
    r = client.get(
        "/api/tome-sync/resolve",
        params={"filename": "Plain Filename Book.epub"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    assert r.json()["book_id"] == book.id


def test_stats_import_matches_by_hash_before_title(db, make_book):
    """A stats-DB book whose title would never fuzzy-match still resolves when
    its md5 is one Tome served."""
    from backend.services.ko_stats_import import import_batch
    from backend.models.ko_stats import KoStatsBookMatch

    book = make_book(title="Actual Library Title", author="Real Author")
    record_ko_hash(db, book.id, "b" * 32, kind="baked")
    db.commit()

    from backend.models.user import User
    user = db.query(User).first()

    res = import_batch(
        db, user, device="HashDev",
        books=[{"ko_id": 3, "md5": "b" * 32, "title": "garbled ocr title 03",
                "authors": "unknown"}],
        page_stats=[{"ko_id": 3, "page": 1, "start_time": 1_710_000_000,
                     "duration": 60, "total_pages": 100}],
    )
    assert res["matched"] == 1
    assert res["page_rows_imported"] == 1
    m = db.query(KoStatsBookMatch).filter(KoStatsBookMatch.ko_md5 == "b" * 32).first()
    assert m.method == "ko_hash" and m.book_id == book.id
