"""The editions pass after an AI series cleanup: give every volume of the
series the ISBN of a text edition.

The rule is the Hardcover sync's (``hardcover_editions.decide_isbn``): an
ISBN known to be an audiobook edition's is replaced by the picked text
edition's, or removed when the book has no other edition with an ISBN; a
missing one is filled; any other stays. The Hardcover book comes from the
volume's match, else from its stored ISBN (``EditionByIsbn``, sanity-checked
like the sync's ISBN match), else from the first metadata-fetch candidate
from Hardcover that agrees with the volume (``identity_match.agrees``). Its
edition list is one ``BookEdition`` query, cached for the run. Writes go
through ``book_updates.apply_isbn_fix`` (the same guard as every other write
path); an ISBN change clears an automatic Hardcover match so the next sync
re-pins the new edition. Uses the instance Hardcover token; without one only
ISBNs already known to be audiobook editions are removed.

Nothing here commits: the caller owns the transaction and the audit entries.
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
from backend.services.book_updates import apply_isbn_fix, can_edit_book
from backend.services.identity_match import agrees
from backend.services.metadata_fetch import HARDCOVER_URL, fetch_candidates

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


async def _gql(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    """One Hardcover query with the instance token; {} when there is none or
    the call fails (one retry on 429)."""
    token = settings.hardcover_token
    if not token:
        return {}
    for attempt in (1, 2):
        try:
            resp = await client.post(HARDCOVER_URL, json={"query": query, "variables": variables},
                                     headers={"authorization": token})
        except httpx.HTTPError as exc:
            logger.info("editions pass: Hardcover call failed (%s)", type(exc).__name__)
            return {}
        if resp.status_code == 429 and attempt == 1:
            await asyncio.sleep(1.5)
            continue
        if resp.status_code >= 400:
            logger.info("editions pass: Hardcover HTTP %s", resp.status_code)
            return {}
        return (resp.json() or {}).get("data") or {}
    return {}


async def _edition_by_isbn(client: httpx.AsyncClient, isbn: str,
                           cache: dict[Any, Any]) -> Optional[dict]:
    """The Hardcover edition carrying this ISBN, or None."""
    from backend.services.hardcover_sync import Q_EDITION_BY_ISBN, isbn_variants

    key = ("isbn", hce.normalize_isbn(isbn))
    if key in cache:
        return cache[key]
    hit = None
    for variant in isbn_variants(isbn):
        editions = (await _gql(client, Q_EDITION_BY_ISBN, {"isbn": variant})).get("editions") or []
        if editions:
            hit = editions[0]
            hce.note_editions([hit])
            break
    cache[key] = hit
    return hit


async def _book_editions(client: httpx.AsyncClient, hc_id: int,
                         cache: dict[Any, Any]) -> list[dict]:
    from backend.services.hardcover_sync import Q_BOOK_EDITION

    key = ("book", hc_id)
    if key not in cache:
        books = (await _gql(client, Q_BOOK_EDITION, {"id": hc_id})).get("books") or []
        cache[key] = (books[0].get("editions") or []) if books else []
    return cache[key]


def _identity(book: Book) -> dict[str, Any]:
    return {"title": book.title, "author": book.author, "series_index": book.series_index}


async def _candidate_book_id(book: Book) -> Optional[int]:
    """The Hardcover book of the first fetch candidate from Hardcover that
    agrees with the volume, or None."""
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
        logger.info("editions pass: fetch failed for book %s (%s)", book.id, type(exc).__name__)
        return None
    for cand in result.candidates:
        if cand.source != "hardcover" or not agrees(_identity(book), cand):
            continue
        try:
            return int(cand.source_id)
        except (TypeError, ValueError):
            continue
    return None


async def _run(books: list[Book]) -> list[dict[str, Any]]:
    from backend.services.hardcover_sync import _isbn_hit_plausible, _lang2

    fixed: list[dict[str, Any]] = []
    cache: dict[Any, Any] = {}
    async with httpx.AsyncClient(timeout=15) as client:
        for book in books:
            editions: list[dict] = []
            hc_id = book.hardcover_book_id if book.hardcover_match_method != "excluded" else None
            if book.isbn:
                hit = await _edition_by_isbn(client, book.isbn, cache)
                if hit:
                    editions.append(hit)
                if hce.isbn_format(book.isbn, editions) != "audiobook":
                    continue  # an ISBN that is not known to be wrong stays
                if hc_id is None and hit:
                    title = (hit.get("book") or {}).get("title") or hit.get("title") or ""
                    if _isbn_hit_plausible(book, title):
                        hc_id = hit.get("book_id") or (hit.get("book") or {}).get("id")
            if hc_id is None:
                hc_id = await _candidate_book_id(book)
            if hc_id is not None:
                editions += await _book_editions(client, int(hc_id), cache)
            fix = hce.decide_isbn(book.isbn, editions, language=_lang2(book.language))
            change = apply_isbn_fix(book, fix) if fix else None
            if change:
                fixed.append(change)
    return fixed


def fix_editions(db: Session, user: User, name: str) -> dict[str, Any]:
    """Check and fix the ISBNs of the series' volumes the user may edit
    (run after an AI series cleanup apply).
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
