"""Import KOReader `statistics.sqlite3` data into Tome (stats-expansion Phase 2.1).

Matching is layered (see docs/plans/stats-expansion-plan.md):
  1. Exact by filename — for books still on the device (esp. TomeSync downloads, which
     save with Tome's own filenames → exact `BookFile.file_path` hit).
  2. Fuzzy title + series + volume — the historical tail (deleted / sideloaded books,
     and the manual-upload path where no device file list exists).

The fuzzy rules were validated against a real Kindle DB (84 strong / 5 review / 7 none of
96, zero high-confidence wrong matches). The crux is *volume-aware* matching: extract the
volume and match on fuzzy series name + EXACT index, or whole multi-volume series collapse
onto one book.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.core.permissions import book_visibility_filter
from backend.models.book import Book, BookFile
from backend.services.ko_hash import lookup_book_ids
from backend.models.ko_stats import KoStatsBookMatch, PageStat, StatsImport

logger = logging.getLogger(__name__)

# NOTE: the import deliberately does NOT write Tome read-status (read/reading/finished).
# Status is user-curation, not telemetry — a fuzzy match plus possibly-incomplete history
# must not flip a hard, library-wide flag. KOReader data feeds *time & pages* only; status
# stays owned by the user and the live position sync.

# ── Title parsing / normalization ─────────────────────────────────────────────

# Decimal capture so "Vol. 2.5" stays 2.5 instead of truncating to 2 (the
# resolve endpoint made the same call) — a half-volume is a different book.
_VOL_RE = re.compile(r"\b(?:vol(?:ume)?\.?|book|tome|part)\s*0*(\d+(?:\.\d+)?)\b", re.I)
# French-style tome abbreviation ("Série T2" / "Série T.2"). Bounded to 1-3
# digits so a standalone "T 1927" year can't be misread as a volume.
_VOL_T_RE = re.compile(r"\bt\.?\s*0*(\d{1,3})\b", re.I)
_MID_RE = re.compile(r"(.*?)\s+0*(\d{1,3})\s*[-–]\s+\S")   # "Series 01 - Subtitle"
_TRAIL_RE = re.compile(r"(.*?)[\s,:–-]+0*(\d{1,3})\s*$")    # "Series 05" / "Series - 02"


def _strip_paren(s: str) -> str:
    return re.sub(r"\([^)]*\)\s*$", "", s or "").strip()


def _desub(s: str) -> str:
    """Drop a real ': ' subtitle but keep title-internal colons like 'Re:ZERO'."""
    return re.split(r":\s+", _strip_paren(s))[0]


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _author_key(a: str) -> str:
    a = (a or "").lower().split("\n")[0]
    if "," in a:                       # "Ugland, Eric" -> "eric ugland"
        last, first = a.split(",", 1)
        a = f"{first.strip()} {last.strip()}"
    return re.sub(r"[^a-z ]", " ", a).strip()


def parse_ko_title(title: str) -> tuple[str, Optional[float]]:
    """-> (normalized base, volume:float|None). Volumes are floats so 2.5
    (half-volumes) survive; integer volumes compare equal to their int form."""
    t = _strip_paren(title or "")
    vol: Optional[float] = None
    m = _VOL_RE.search(t)
    mt = _VOL_T_RE.search(t)
    if m:
        vol = float(m.group(1))
        base = _VOL_RE.sub(" ", t)
    elif mt:
        vol = float(mt.group(1))
        base = _VOL_T_RE.sub(" ", t)
    elif _MID_RE.match(t):
        m3 = _MID_RE.match(t); base, vol = m3.group(1), float(m3.group(2))
    elif _TRAIL_RE.match(t):
        m2 = _TRAIL_RE.match(t); base, vol = m2.group(1), float(m2.group(2))
    else:
        base = t
    return _norm(_desub(base)), vol


# ── Pure matcher ──────────────────────────────────────────────────────────────

@dataclass
class BookCandidate:
    id: int
    title: str
    author: Optional[str]
    series: Optional[str]
    series_index: Optional[float]


@dataclass
class MatchResult:
    book_id: Optional[int]
    confidence: float
    method: str   # 'filename' | 'fuzzy' | 'none'
    status: str   # 'matched' | 'review' | 'unmatched'


STRONG = 0.9
STRONG_WITH_AUTHOR = 0.8
REVIEW = 0.6


def _basename(p: str) -> str:
    return os.path.basename((p or "").replace("\\", "/")).strip().lower()


def _candidate_volumes(c: BookCandidate) -> set[float]:
    """Every volume number a candidate credibly claims: its ``series_index``
    and any volume parsed out of its own title. Empty set = no volume signal
    (standalones, distinct-title volumes)."""
    vols: set[float] = set()
    if c.series_index is not None:
        vols.add(float(c.series_index))
    title_vol = parse_ko_title(c.title or "")[1]
    if title_vol is not None:
        vols.add(title_vol)
    return vols


def match_book(
    candidates: list[BookCandidate],
    ko_title: str,
    ko_authors: Optional[str],
    *,
    filename: Optional[str] = None,
    path_index: Optional[dict[str, int]] = None,
) -> MatchResult:
    """Resolve a KOReader book to a Tome book id. Pure — no DB access."""
    # Layer 1: exact filename (basename of a known BookFile path).
    if filename and path_index:
        bid = path_index.get(_basename(filename))
        if bid is not None:
            return MatchResult(bid, 1.0, "filename", "matched")

    kbase, kvol = parse_ko_title(ko_title)
    kauth = _author_key(ko_authors or "")
    knt = _norm(_desub(ko_title or ""))

    # Pre-index candidates by (norm series, float index) and norm title.
    # Float keys so half-volumes (2.5) are exact-matchable like integers.
    series_vol: dict[tuple[str, float], list[BookCandidate]] = {}
    series_names: set[str] = set()
    for c in candidates:
        if c.series and c.series_index is not None:
            key = (_norm(c.series), float(c.series_index))
            series_vol.setdefault(key, []).append(c)
            series_names.add(_norm(c.series))

    best = 0.0
    best_c: Optional[BookCandidate] = None
    best_auth = False

    # Layer 2: volume-aware — fuzzy series name + EXACT index.
    parsed_vol_candidate = False
    if kvol is not None and series_names:
        bs_name, bs_ratio = None, 0.0
        for ns in series_names:
            r = SequenceMatcher(None, kbase, ns).ratio()
            if r > bs_ratio:
                bs_ratio, bs_name = r, ns
        if bs_name and (bs_name, kvol) in series_vol:
            cands = series_vol[(bs_name, kvol)]
            c = max(
                cands,
                key=lambda x: SequenceMatcher(None, kauth, _author_key(x.author or "")).ratio()
                if kauth else 0.0,
            )
            best, best_c = bs_ratio, c
            best_auth = bool(kauth and SequenceMatcher(None, kauth, _author_key(c.author or "")).ratio() > 0.8)
            parsed_vol_candidate = True

    # Layer 3: plain title fuzzy (distinct-title volumes like "Dungeon Mauling").
    # A parsed KO volume is authoritative here too (issue #152): sibling volumes
    # whose titles differ only in the digit ("… Book 2" vs "… Book 5") score
    # 0.95+, and same-titled series volumes score 1.0 after de-subtitling — so a
    # volume Tome doesn't own would strong-match a sibling and silently import
    # a whole book's reading history onto it. A candidate claiming a different
    # volume (via series_index or its own title) is excluded; candidates with no
    # volume signal stay eligible so standalones and distinct-title volumes
    # still match. Better parked-unmatched than confidently wrong.
    for c in candidates:
        if kvol is not None:
            cvols = _candidate_volumes(c)
            if cvols and kvol not in cvols:
                continue
        r = SequenceMatcher(None, knt, _norm(_desub(c.title or ""))).ratio()
        if r > best:
            best, best_c = r, c
            best_auth = bool(kauth and SequenceMatcher(None, kauth, _author_key(c.author or "")).ratio() > 0.8)

    if best_c is None:
        return MatchResult(None, 0.0, "none", "unmatched")

    if best >= STRONG or (best >= STRONG_WITH_AUTHOR and best_auth):
        status = "matched"
    elif best >= REVIEW or parsed_vol_candidate:
        # A confidently-parsed volume with an exact (series,index) hit is never silently
        # dropped — surface it for review even on a weak series-name score.
        status = "review"
    else:
        return MatchResult(None, best, "none", "unmatched")
    return MatchResult(best_c.id, round(best, 4), "fuzzy", status)


# ── DB orchestration ──────────────────────────────────────────────────────────

def _load_candidates(db: Session, user) -> tuple[list[BookCandidate], dict[str, int]]:
    """Visible active books + a basename→book_id index for exact filename matching."""
    rows = (
        db.query(Book.id, Book.title, Book.author, Book.series, Book.series_index)
        .filter(Book.status == "active", book_visibility_filter(db, user))
        .all()
    )
    candidates = [BookCandidate(*r) for r in rows]
    ids = [c.id for c in candidates]
    path_index: dict[str, int] = {}
    if ids:
        for bid, path in db.query(BookFile.book_id, BookFile.file_path).filter(BookFile.book_id.in_(ids)):
            base = _basename(path)
            if base:
                path_index.setdefault(base, bid)
    return candidates, path_index


def import_batch(
    db: Session,
    user,
    *,
    device: str,
    books: list[dict],
    page_stats: list[dict],
) -> dict:
    """Match a batch of KOReader books and ingest their per-page dwell rows idempotently.

    `books`: dicts with ko_id, md5, title, authors, (optional) series, filename, pages,
             total_read_pages.
    `page_stats`: dicts with ko_id, page, start_time, duration, total_pages.

    Status is NOT written — read/reading/finished stays user-curated. Only *confident*
    (matched) books contribute data; the review tail and unmatched books are parked, so
    nothing uncertain reaches the dashboard.
    """
    candidates, path_index = _load_candidates(db, user)

    ko_to_book: dict[int, Optional[int]] = {}
    counts = {"matched": 0, "review": 0, "unmatched": 0}
    # Per-batch md5 cache. KOReader re-downloads create multiple `book` rows sharing
    # one partial md5; the server session is autoflush=False, so a query won't see a
    # pending add — without this we'd INSERT two rows for the same (user, md5) and the
    # UNIQUE constraint would blow up the whole batch. Maps md5 -> resolved book_id.
    seen_md5: dict[str, Optional[int]] = {}

    for b in books:
        ko_id = b["ko_id"]
        md5 = b.get("md5") or ""

        if md5 and md5 in seen_md5:
            ko_to_book[ko_id] = seen_md5[md5]   # same file already handled this batch
            continue

        existing = (
            db.query(KoStatsBookMatch)
            .filter(KoStatsBookMatch.user_id == user.id, KoStatsBookMatch.ko_md5 == md5)
            .first()
        ) if md5 else None

        if existing and existing.confirmed:
            ko_to_book[ko_id] = existing.book_id
            counts["matched" if existing.book_id else "unmatched"] += 1
            if md5:
                seen_md5[md5] = existing.book_id
            continue

        # Deterministic identity first: KOReader's md5 IS the partial-MD5 Tome
        # records for every artifact it scans or serves (ko_hashes), so a hash
        # hit is exact — no title heuristics, works for renamed files. The
        # fuzzy matcher below stays as the fallback for history whose files
        # never came from (or into) this library.
        if md5:
            hash_hit = lookup_book_ids(db, [md5]).get(md5)
            if hash_hit is not None:
                ko_to_book[ko_id] = hash_hit
                counts["matched"] += 1
                if existing:
                    existing.book_id = hash_hit
                    existing.confidence = 1.0
                    existing.method = "ko_hash"
                    existing.status = "matched"
                    existing.ko_title = b.get("title")
                    existing.ko_authors = b.get("authors")
                else:
                    db.add(KoStatsBookMatch(
                        user_id=user.id, ko_md5=md5,
                        ko_title=b.get("title"), ko_authors=b.get("authors"),
                        book_id=hash_hit, confidence=1.0,
                        method="ko_hash", status="matched",
                    ))
                seen_md5[md5] = hash_hit
                continue

        res = match_book(
            candidates, b.get("title") or "", b.get("authors"),
            filename=b.get("filename"), path_index=path_index,
        )
        # Only confident matches contribute time data; review/unmatched are parked.
        resolved = res.book_id if res.status == "matched" else None
        ko_to_book[ko_id] = resolved
        counts[res.status] += 1

        # An empty md5 can't key the cache table; map the book but don't persist a row.
        if md5:
            if existing:
                existing.book_id = res.book_id
                existing.confidence = res.confidence
                existing.method = res.method
                existing.status = res.status
                existing.ko_title = b.get("title")
                existing.ko_authors = b.get("authors")
            else:
                db.add(KoStatsBookMatch(
                    user_id=user.id, ko_md5=md5,
                    ko_title=b.get("title"), ko_authors=b.get("authors"),
                    book_id=res.book_id, confidence=res.confidence,
                    method=res.method, status=res.status,
                ))
            seen_md5[md5] = resolved

    # Idempotent page-stat ingest: INSERT OR IGNORE on the identity unique constraint.
    rows = []
    max_start = 0
    for ps in page_stats:
        bid = ko_to_book.get(ps["ko_id"])
        if bid is None:
            continue
        st = int(ps["start_time"])
        max_start = max(max_start, st)
        rows.append({
            "user_id": user.id, "book_id": bid,
            "page": int(ps["page"]), "total_pages": int(ps.get("total_pages") or 0),
            "start_time": st, "duration_seconds": int(ps.get("duration") or 0),
            "device": device or "",
        })

    imported = 0
    if rows:
        for chunk in (rows[i:i + 500] for i in range(0, len(rows), 500)):
            stmt = sqlite_insert(PageStat).values(chunk).on_conflict_do_nothing(
                index_elements=["user_id", "book_id", "page", "start_time", "device"]
            )
            imported += db.execute(stmt).rowcount or 0

    # Per-device watermark.
    wm = (
        db.query(StatsImport)
        .filter(StatsImport.user_id == user.id, StatsImport.device == (device or ""))
        .first()
    )
    if wm:
        wm.last_start_time_synced = max(wm.last_start_time_synced, max_start)
        wm.rows_imported += imported
        wm.last_run_at = datetime.utcnow()
    else:
        db.add(StatsImport(
            user_id=user.id, device=device or "",
            last_start_time_synced=max_start, rows_imported=imported,
        ))

    db.commit()
    return {
        "books": len(books),
        "matched": counts["matched"],
        "review": counts["review"],
        "unmatched": counts["unmatched"],
        "page_rows_imported": imported,
        "page_rows_skipped": len(rows) - imported,
        "watermark": max_start,
    }


# ── Startup repair: re-verify stored fuzzy matches (issue #152) ───────────────

def repair_fuzzy_matches(db: Session) -> dict:
    """Re-run the (volume-guarded) matcher over every stored fuzzy match and
    clean up the fallout of matches the old matcher got wrong.

    Before the Layer-3 volume guard, a volume Tome doesn't own could
    strong-match a sibling volume and import a whole book's page-stats onto it
    (issue #152: vol 2's 20 h landed on the never-opened vol 5). The wrong
    verdicts are recoverable because every fuzzy match row stores the KO title
    and authors it was decided from.

    For each non-confirmed ``method='fuzzy', status='matched'`` row, resolve it
    again — hash first (deterministic, mirrors ``import_batch``), then the
    fixed matcher. When the verdict changes, the row is updated and the
    previously-matched book's page-stats are handled conservatively:

    - If no other match row (for that user) still points at the old book, all
      of its rows came from the bad match → delete them, reset the user's
      device watermarks so the next sync re-uploads history under the fixed
      rules, and notify the user.
    - Otherwise the book mixes ghost and genuine rows, which cannot be told
      apart (``total_pages`` shifts with device re-renders, so it can't
      partition them) → keep everything and notify the user to use
      "Clear imported history" on the book page if it looks wrong.

    Idempotent: repaired rows no longer satisfy the filter, so the next
    startup is a no-op. Never raises — callers run it fire-and-forget.
    """
    from backend.models.book import Book as _Book
    from backend.models.notification import Notification
    from backend.models.user import User

    rows = (
        db.query(KoStatsBookMatch)
        .filter(
            KoStatsBookMatch.method == "fuzzy",
            KoStatsBookMatch.status == "matched",
            KoStatsBookMatch.confirmed.is_(False),
            KoStatsBookMatch.book_id.isnot(None),
        )
        .all()
    )
    result = {"checked": len(rows), "changed": 0, "pages_deleted": 0, "kept_mixed": 0}
    if not rows:
        return result

    cand_cache: dict[int, tuple[list[BookCandidate], dict[str, int]]] = {}
    # (user_id, old_book_id) pairs whose attribution was revoked — cleanup runs
    # after every row is re-verdicted so "does anything else point at this
    # book" sees the final state, not a half-updated one.
    revoked: set[tuple[int, int]] = set()

    for row in rows:
        user = db.get(User, row.user_id)
        if user is None:
            continue
        if user.id not in cand_cache:
            cand_cache[user.id] = _load_candidates(db, user)
        candidates, _path_index = cand_cache[user.id]

        old_book_id = row.book_id
        hash_hit = lookup_book_ids(db, [row.ko_md5]).get(row.ko_md5) if row.ko_md5 else None
        if hash_hit is not None:
            new_book_id, confidence, method, status = hash_hit, 1.0, "ko_hash", "matched"
        else:
            res = match_book(candidates, row.ko_title or "", row.ko_authors)
            new_book_id = res.book_id if res.status == "matched" else None
            confidence, method, status = res.confidence, res.method, res.status

        if new_book_id == old_book_id and status == "matched":
            continue  # the match still stands under the fixed rules

        row.book_id = new_book_id
        row.confidence = confidence
        row.method = method
        row.status = status
        result["changed"] += 1
        revoked.add((row.user_id, old_book_id))

    db.flush()

    for user_id, book_id in sorted(revoked):
        pages = (
            db.query(PageStat)
            .filter(PageStat.user_id == user_id, PageStat.book_id == book_id)
            .count()
        )
        if pages == 0:
            continue
        book = db.get(_Book, book_id)
        title = book.title if book else "a deleted book"
        # Only 'matched' rows ever imported pages — a parked review row also
        # carries a book_id but contributed nothing, so it must not block cleanup.
        other_sources = (
            db.query(KoStatsBookMatch.id)
            .filter(
                KoStatsBookMatch.user_id == user_id,
                KoStatsBookMatch.book_id == book_id,
                KoStatsBookMatch.status == "matched",
            )
            .first()
        )
        if other_sources is None:
            # The bad match was this book's only import source — every row is ghost.
            db.query(PageStat).filter(
                PageStat.user_id == user_id, PageStat.book_id == book_id
            ).delete(synchronize_session=False)
            # Full re-upload on next sync rebuilds anything the bad match starved
            # (rows behind the watermark that now resolve elsewhere). Idempotent
            # ingest makes the re-send a no-op for everything already correct.
            db.query(StatsImport).filter(StatsImport.user_id == user_id).update(
                {StatsImport.last_start_time_synced: 0}, synchronize_session=False
            )
            result["pages_deleted"] += pages
            db.add(Notification(
                user_id=user_id,
                kind="stats_repair",
                title=f'Removed misattributed reading history from "{title}"',
                body=(
                    "An earlier import matched another book's KOReader history to "
                    "this book. The wrong entries were removed; your device will "
                    "re-sync its history on the next connection."
                ),
                link=f"/books/{book_id}" if book else None,
            ))
        else:
            result["kept_mixed"] += 1
            db.add(Notification(
                user_id=user_id,
                kind="stats_repair",
                title=f'Reading history on "{title}" may include another book\'s sessions',
                body=(
                    "An earlier import matched another book's KOReader history to "
                    "this book, which also has correctly imported history. The two "
                    "cannot be told apart automatically. If the numbers look wrong, "
                    "use \"Clear imported history\" in the book's Reading Stats."
                ),
                link=f"/books/{book_id}" if book else None,
            ))

    db.commit()
    return result


def repair_fuzzy_matches_startup() -> None:
    """Daemon-thread entry point: run the repair against a fresh session and
    swallow everything — bookkeeping must never take the server down."""
    from backend.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            result = repair_fuzzy_matches(db)
        if result["changed"]:
            logger.info(
                "ko-stats repair: %(checked)d fuzzy matches checked, "
                "%(changed)d corrected, %(pages_deleted)d ghost page-stats deleted, "
                "%(kept_mixed)d mixed books kept for manual review", result,
            )
    except Exception:
        logger.exception("ko-stats fuzzy-match repair failed")
