"""KOReader partial-MD5: compute and record device-matchable file identities.

KOReader identifies a book by ``util.partialMD5`` — an MD5 over 1 KB samples
taken at exponentially spaced offsets — not by a full-content hash. This module
is a byte-exact Python port plus the recording helpers that keep the
``ko_hashes`` table current (see the model docstring for why raw and baked
artifacts are hashed separately).

Port notes (KOReader ``frontend/util.lua:1111``):
- the loop runs i = -1 .. 10 with ``bit.lshift(1024, 2*i)``; LuaJIT masks the
  shift amount to 5 bits, so i = -1 becomes a shift by 30 whose 32-bit result
  is 0 — i.e. the first sample is at offset 0, then 1024 << (2*i) for i >= 0
  (1 KB, 4 KB, 16 KB, … 1 GB).
- ``file:read(1024)`` returns nil only at EOF; a short read near EOF is still
  hashed. Python's ``read()`` returning ``b""`` maps exactly onto the nil case.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.models.ko_stats import KoHash

logger = logging.getLogger(__name__)

# Baked bytes change whenever metadata changes; devices keep whatever they
# downloaded. Retain the last few baked hashes per book so older copies still
# resolve, without growing without bound.
BAKED_HASHES_KEPT = 5


def ko_partial_md5(path: str | Path) -> str | None:
    """KOReader's util.partialMD5 for a local file; None if unreadable."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for i in range(-1, 11):
                offset = 0 if i == -1 else 1024 << (2 * i)
                f.seek(offset)
                sample = f.read(1024)
                if sample:
                    h.update(sample)
                else:
                    break
    except OSError as exc:
        logger.warning("partial-md5 failed for %s: %s", path, exc)
        return None
    return h.hexdigest()


def record_ko_hash(db: Session, book_id: int, md5: str | None, kind: str = "raw") -> None:
    """Idempotently record a hash for a book; prunes old baked hashes.

    Callers pass the output of :func:`ko_partial_md5` (None is a no-op so hook
    sites don't need their own guards). Commits are left to the caller — hook
    sites run inside larger transactions.
    """
    if not md5:
        return
    existing = (
        db.query(KoHash)
        .filter(KoHash.book_id == book_id, KoHash.ko_partial_md5 == md5)
        .first()
    )
    if existing:
        return
    # The server session runs autoflush=False, so a query cannot see a row that
    # is still pending here (e.g. the same hash recorded twice earlier in one
    # scan transaction). Guard against queuing a duplicate INSERT.
    for pending in db.new:
        if (
            isinstance(pending, KoHash)
            and pending.book_id == book_id
            and pending.ko_partial_md5 == md5
        ):
            return
    db.add(KoHash(book_id=book_id, ko_partial_md5=md5, kind=kind))
    if kind == "baked":
        stale = (
            db.query(KoHash.id)
            .filter(KoHash.book_id == book_id, KoHash.kind == "baked")
            .order_by(KoHash.created_at.desc(), KoHash.id.desc())
            .offset(BAKED_HASHES_KEPT)  # autoflush ranks the pending row newest
            .all()
        )
        if stale:
            db.execute(delete(KoHash).where(KoHash.id.in_([r.id for r in stale])))


def lookup_book_ids(db: Session, md5s: list[str]) -> dict[str, int]:
    """Batch resolve partial-MD5s → book ids (first match wins per hash)."""
    if not md5s:
        return {}
    out: dict[str, int] = {}
    rows = (
        db.query(KoHash.ko_partial_md5, KoHash.book_id)
        .filter(KoHash.ko_partial_md5.in_(md5s))
        .order_by(KoHash.id)
        .all()
    )
    for md5, book_id in rows:
        out.setdefault(md5, book_id)
    return out


def record_served_artifact(db: Session, book_id: int, book_file, served_path) -> None:
    """Record the partial-MD5 of the bytes a device is about to receive.

    Called from every download path after ``get_baked_path()``. The served
    file is the baked cache normally, or the raw library file when baking
    fell back (or an in-place bake made the raw file current) — either way,
    THIS is the artifact whose hash a KOReader device will later present,
    so hash exactly what goes over the wire.
    """
    import os
    kind = "raw" if os.path.samefile(served_path, book_file.file_path) else "baked"
    try:
        record_ko_hash(db, book_id, ko_partial_md5(served_path), kind)
        db.commit()
    except Exception as exc:  # never let bookkeeping break a download
        logger.warning("ko-hash record failed for book %s: %s", book_id, exc)
        db.rollback()


def backfill_missing_raw_hashes() -> None:
    """One-shot startup backfill: hash library files that predate ko_hashes.

    Partial-MD5 reads at most ~12 KB per file, so even large libraries finish
    in seconds; run in a daemon thread anyway so startup never blocks on a
    slow network mount. Files already hashed (any kind) are skipped, making
    every run after the first a no-op.
    """
    from backend.core.database import SessionLocal
    from backend.models.book import BookFile

    with SessionLocal() as db:
        missing = (
            db.query(BookFile)
            .outerjoin(KoHash, (KoHash.book_id == BookFile.book_id) & (KoHash.kind == "raw"))
            .filter(KoHash.id.is_(None))
            .all()
        )
        if not missing:
            return
        done = 0
        for bf in missing:
            record_ko_hash(db, bf.book_id, ko_partial_md5(bf.file_path), "raw")
            done += 1
            if done % 200 == 0:
                db.commit()
        db.commit()
        logger.info("ko-hash backfill: hashed %d library files", done)
