"""Goodreads / StoryGraph CSV import — reading history for books Tome holds.

Two-step, stateless flow:
  parse_csv()  -> dialect-detected rows (read / currently-reading only)
  match_rows() -> proposals against the caller's visible library
                  (ISBN first, then normalized title+author, then fuzzy title)
The API layer previews the proposals; applying is fill-gaps-only (see the
endpoint) so an import can never overwrite live sync state or curation.

Only read/currently-reading rows are considered: "to read" piles belong to
the wishlist, not reading history — deliberately out of scope here.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

MAX_ROWS = 5000

# Column names that identify each dialect (case-sensitive as exported).
_GOODREADS_MARKERS = {"Exclusive Shelf", "My Rating"}
_STORYGRAPH_MARKERS = {"Read Status", "Star Rating"}


class ImportRow(dict):
    """title, author, isbn, status ('read'|'reading'), rating (float|None),
    finished_on (date-iso|None), review (str|None), line (int)"""


def _clean_isbn(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    digits = re.sub(r"[^0-9Xx]", "", raw)
    return digits.upper() or None


def _parse_date(raw: str | None) -> Optional[str]:
    if not raw or not raw.strip():
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    # Drop subtitle/series decorations Goodreads loves: "Title (Series, #3)"
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def parse_csv(data: bytes) -> tuple[str, list[ImportRow], int]:
    """-> (dialect, rows, skipped_unread). Raises ValueError on unknown shape."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    if _GOODREADS_MARKERS <= headers:
        dialect = "goodreads"
    elif _STORYGRAPH_MARKERS <= headers:
        dialect = "storygraph"
    else:
        raise ValueError(
            "Unrecognized CSV — expected a Goodreads library export or a StoryGraph export"
        )

    rows: list[ImportRow] = []
    skipped = 0
    for i, rec in enumerate(reader):
        if i >= MAX_ROWS:
            break
        if dialect == "goodreads":
            shelf = (rec.get("Exclusive Shelf") or "").strip().lower()
            status = {"read": "read", "currently-reading": "reading"}.get(shelf)
            if status is None:
                skipped += 1
                continue
            rating_raw = (rec.get("My Rating") or "0").strip()
            rating = float(rating_raw) if rating_raw not in ("", "0") else None
            rows.append(ImportRow(
                title=(rec.get("Title") or "").strip(),
                author=(rec.get("Author") or "").strip(),
                isbn=_clean_isbn(rec.get("ISBN13") or rec.get("ISBN")),
                status=status,
                rating=rating,
                finished_on=_parse_date(rec.get("Date Read")) if status == "read" else None,
                review=(rec.get("My Review") or "").strip() or None,
                line=i + 2,
            ))
        else:
            rs = (rec.get("Read Status") or "").strip().lower()
            status = {"read": "read", "currently-reading": "reading"}.get(rs)
            if status is None:
                skipped += 1
                continue
            rating_raw = (rec.get("Star Rating") or "").strip()
            rating = float(rating_raw) if rating_raw else None
            rows.append(ImportRow(
                title=(rec.get("Title") or "").strip(),
                author=(rec.get("Authors") or rec.get("Author") or "").strip(),
                isbn=_clean_isbn(rec.get("ISBN/UID") or rec.get("ISBN")),
                status=status,
                rating=rating,
                finished_on=_parse_date(rec.get("Last Date Read")) if status == "read" else None,
                review=(rec.get("Review") or "").strip() or None,
                line=i + 2,
            ))
    return dialect, rows, skipped


def match_rows(rows: list[ImportRow], books: list) -> tuple[list[dict], list[dict]]:
    """Match rows against Book ORM objects (already visibility-filtered).

    -> (matched, unmatched). matched: {row fields..., book_id, matched_title,
    match_via}. First ISBN, then exact normalized title+author-overlap, then
    fuzzy title (ratio >= 0.88) with author overlap."""
    by_isbn: dict[str, object] = {}
    for b in books:
        isbn = _clean_isbn(getattr(b, "isbn", None))
        if isbn:
            by_isbn.setdefault(isbn, b)
    normed = [(b, _norm(b.title), _norm(b.author)) for b in books]

    matched: list[dict] = []
    unmatched: list[dict] = []
    for row in rows:
        book = None
        via = None
        if row["isbn"] and row["isbn"] in by_isbn:
            book, via = by_isbn[row["isbn"]], "isbn"
        if book is None:
            nt, na = _norm(row["title"]), _norm(row["author"])
            na_tokens = set(na.split())
            for b, bt, ba in normed:
                if bt == nt and (not na_tokens or na_tokens & set(ba.split())):
                    book, via = b, "title"
                    break
            if book is None and nt:
                best, best_ratio = None, 0.0
                for b, bt, ba in normed:
                    if na_tokens and not (na_tokens & set(ba.split())):
                        continue
                    ratio = SequenceMatcher(None, nt, bt).ratio()
                    if ratio > best_ratio:
                        best, best_ratio = b, ratio
                if best is not None and best_ratio >= 0.88:
                    book, via = best, "fuzzy"
        if book is None:
            unmatched.append(dict(row))
        else:
            matched.append({
                **row,
                "book_id": book.id,
                "matched_title": book.title,
                "matched_author": book.author,
                "match_via": via,
            })
    return matched, unmatched
