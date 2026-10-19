"""Fix editions: give every volume of a series the ISBN of a text edition.

A volume needs a fix when it has no ISBN, or when its stored ISBN is an
audiobook edition (one Hardcover ``EditionByIsbn`` lookup per stored ISBN,
cached for the run; ISBNs the metadata fetch already saw as audiobooks need no
lookup). For each such volume the metadata fetch runs on its title, author,
series and volume number; the first candidate that agrees with the volume
(``identity_match.agrees``) and carries an ISBN that is not an audiobook
edition's supplies the ISBN, plus publisher and year where the book has none.
Writes go through ``book_updates.write_isbn`` (the same guard as every other
write path). A volume with an audiobook ISBN and no agreeing candidate has the
ISBN removed: it is known to be wrong, and every lookup keyed on it fails.

Run by the AI series cleanup after its apply, and on its own through
``POST /api/series/{name}/fix-editions`` for people without an AI key.
Nothing here commits: the caller owns the transaction and the audit entry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.permissions import book_visibility_filter
from backend.models.book import Book
from backend.models.user import User
from backend.services import hardcover_editions as hce
from backend.services.book_updates import can_edit_book, isbn_allowed, write_isbn
from backend.services.identity_match import agrees
from backend.services.metadata_fetch import HARDCOVER_URL, MetadataCandidate, fetch_candidates

logger = logging.getLogger(__name__)

NO_SERIES_SENTINEL = "__unserialized__"


def series_books(db: Session, user: User, name: str) -> list[Book]:
    """Every active book in the series the user can see, in volume order."""
    return (
        db.query(Book)
        .filter(Book.status == "active", Book.series == name, book_visibility_filter(db, user))
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc(), Book.id.asc())
        .all()
    )


async def _lookup_format(client: httpx.AsyncClient, isbn: str,
                         cache: dict[str, Optional[str]]) -> Optional[str]:
    """The edition format Hardcover gives this ISBN, or None when unknown
    (no token, no hit, an error)."""
    key = hce.normalize_isbn(isbn)
    if not key:
        return None
    if key in cache:
        return cache[key]
    if hce.is_known_audio(key):
        cache[key] = "audiobook"
        return "audiobook"
    token = settings.hardcover_token
    if not token:
        return None
    from backend.services.hardcover_sync import Q_EDITION_BY_ISBN

    variants = [key] + ([hce.isbn10_to_13(key)] if len(key) == 10 and hce.isbn10_to_13(key) else [])
    fmt: Optional[str] = None
    for variant in variants:
        data: dict[str, Any] = {}
        for attempt in (1, 2):
            try:
                resp = await client.post(
                    HARDCOVER_URL,
                    json={"query": Q_EDITION_BY_ISBN, "variables": {"isbn": variant}},
                    headers={"authorization": token},
                )
            except httpx.HTTPError as exc:
                logger.info("fix-editions: ISBN lookup failed (%s)", type(exc).__name__)
                break
            if resp.status_code == 429 and attempt == 1:
                await asyncio.sleep(1.5)
                continue
            if resp.status_code >= 400:
                logger.info("fix-editions: ISBN lookup HTTP %s", resp.status_code)
                break
            data = (resp.json() or {}).get("data") or {}
            break
        editions = data.get("editions") or []
        if editions:
            fmt = hce.edition_format(editions[0])
            if fmt == "audiobook":
                hce.note_audio_isbn(key)
            break
    cache[key] = fmt
    return fmt


def _identity(book: Book) -> dict[str, Any]:
    return {"title": book.title, "author": book.author, "series_index": book.series_index}


async def _pick_candidate(client: httpx.AsyncClient, book: Book,
                          cache: dict[str, Optional[str]]) -> Optional[MetadataCandidate]:
    try:
        result = await fetch_candidates(
            title=book.title,
            author=book.author,
            isbn=None,
            series=book.series,
            series_index=book.series_index,
            year=book.year,
            language=book.language,
            media_hint=book.book_type.slug if book.book_type else None,
        )
    except Exception as exc:  # noqa: BLE001 - one volume never fails the run
        logger.info("fix-editions: fetch failed for book %s (%s)", book.id, type(exc).__name__)
        return None
    current = hce.normalize_isbn(book.isbn)
    for cand in result.candidates:
        isbn = hce.normalize_isbn(cand.isbn)
        if not isbn or isbn == current:
            continue
        if not isbn_allowed(cand.isbn, cand.edition_format):
            continue
        if not agrees(_identity(book), cand):
            continue
        if cand.edition_format not in ("ebook", "physical"):
            # Not from a Hardcover edition list: check what the ISBN is.
            if await _lookup_format(client, isbn, cache) == "audiobook":
                continue
        return cand
    return None


async def _run(books: list[Book]) -> list[dict[str, Any]]:
    fixed: list[dict[str, Any]] = []
    cache: dict[str, Optional[str]] = {}
    async with httpx.AsyncClient(timeout=15) as client:
        for book in books:
            old = book.isbn
            if old:
                if await _lookup_format(client, old, cache) != "audiobook":
                    continue  # an ISBN that is not known to be wrong stays
                reason = "audiobook"
            else:
                reason = "missing"
            cand = await _pick_candidate(client, book, cache)
            if cand is not None and write_isbn(book, cand.isbn, cand.edition_format):
                if cand.publisher and not book.publisher:
                    book.publisher = cand.publisher
                if cand.year and not book.year:
                    book.year = cand.year
                fixed.append({"book_id": book.id, "title": book.title,
                              "from": old, "to": book.isbn, "reason": reason})
            elif reason == "audiobook":
                book.isbn = None
                fixed.append({"book_id": book.id, "title": book.title,
                              "from": old, "to": None, "reason": reason})
    return fixed


def fix_editions(db: Session, user: User, name: str) -> dict[str, Any]:
    """Check and fix the ISBNs of the series' volumes the user may edit.
    Returns ``{"checked": n, "isbns_fixed": [{book_id, title, from, to,
    reason}]}``. Flushes; the caller commits and audits."""
    if not name or name == NO_SERIES_SENTINEL:
        return {"checked": 0, "isbns_fixed": []}
    books = [b for b in series_books(db, user, name)
             if can_edit_book(user, b) and b.content_type != "chapter"]
    if not books:
        return {"checked": 0, "isbns_fixed": []}
    fixed = asyncio.run(_run(books))
    if fixed:
        db.flush()
        from backend.services.fts import index_book
        for book in books:
            if any(f["book_id"] == book.id for f in fixed):
                index_book(db, book)
    return {"checked": len(books), "isbns_fixed": fixed}
