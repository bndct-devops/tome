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
from backend.models.ko_stats import KoStatsBookMatch, PageStat, StatsImport

# NOTE: the import deliberately does NOT write Tome read-status (read/reading/finished).
# Status is user-curation, not telemetry — a fuzzy match plus possibly-incomplete history
# must not flip a hard, library-wide flag. KOReader data feeds *time & pages* only; status
# stays owned by the user and the live position sync.

# ── Title parsing / normalization ─────────────────────────────────────────────

_VOL_RE = re.compile(r"\b(?:vol(?:ume)?\.?|book|tome|part)\s*0*(\d+)\b", re.I)
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


def parse_ko_title(title: str) -> tuple[str, Optional[int]]:
    """-> (normalized base, volume:int|None)."""
    t = _strip_paren(title or "")
    vol: Optional[int] = None
    m = _VOL_RE.search(t)
    if m:
        vol = int(m.group(1))
        base = _VOL_RE.sub(" ", t)
    elif _MID_RE.match(t):
        m3 = _MID_RE.match(t); base, vol = m3.group(1), int(m3.group(2))
    elif _TRAIL_RE.match(t):
        m2 = _TRAIL_RE.match(t); base, vol = m2.group(1), int(m2.group(2))
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

    # Pre-index candidates by (norm series, int index) and norm title.
    series_vol: dict[tuple[str, int], list[BookCandidate]] = {}
    series_names: set[str] = set()
    for c in candidates:
        if c.series and c.series_index is not None and float(c.series_index).is_integer():
            key = (_norm(c.series), int(c.series_index))
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
    for c in candidates:
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

    for b in books:
        ko_id = b["ko_id"]
        md5 = b.get("md5") or ""
        existing = (
            db.query(KoStatsBookMatch)
            .filter(KoStatsBookMatch.user_id == user.id, KoStatsBookMatch.ko_md5 == md5)
            .first()
        )
        if existing and existing.confirmed:
            ko_to_book[ko_id] = existing.book_id
            counts["matched" if existing.book_id else "unmatched"] += 1
            continue

        res = match_book(
            candidates, b.get("title") or "", b.get("authors"),
            filename=b.get("filename"), path_index=path_index,
        )
        # Only confident matches contribute time data; review/unmatched are parked.
        ko_to_book[ko_id] = res.book_id if res.status == "matched" else None
        counts[res.status] += 1

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
