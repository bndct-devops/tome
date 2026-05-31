"""Wish matcher — finds open wishes that match a newly added book, and
(reverse direction) finds existing books that match an open wish.

Forward strategies (book just added → find matching wishes):
1. ISBN exact — strong.
2. Series + index — strong (single-volume wish only; series-wishes stay open).
3. Title + author fuzzy (SequenceMatcher > 0.85) — weak, suggestion only.

Reverse strategies (wish just created → find matching books already in library):
1. ISBN exact — strong.
2. Whole-series wish — books where series matches OR title starts with series name.
3. Single-volume series wish — books where series+index both match exactly.
4. Standalone fuzzy — title+author SequenceMatcher > 0.85 (only when cheaper
   strategies found nothing; scans the full library but caps results).

The matcher NEVER auto-fulfils. It populates Wish.suggested_book_ids so the
admin fulfil-panel can surface "this satisfies Maya's wish — Fulfill?".
"""
from __future__ import annotations

import difflib
import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.models.wish import Wish

if TYPE_CHECKING:
    from backend.models.book import Book

log = logging.getLogger(__name__)

# Same threshold as admin_duplicates.py
_FUZZY_THRESHOLD = 0.85

# Maximum suggestions returned by find_matching_books
_MAX_BOOK_SUGGESTIONS = 10


def find_matching_wishes(db: Session, book: "Book") -> list[Wish]:
    """Return all open wishes that match the given book.

    A series-wish (series set, no series_index on the wish) is included when
    the book belongs to that series — but the wish is NOT auto-fulfilled; it
    surfaces a suggestion only.

    A single-volume wish (series_index present) is included only when the
    book's series AND series_index match exactly.
    """
    open_wishes: list[Wish] = (
        db.query(Wish).filter(Wish.status == "open").all()
    )

    matched: list[Wish] = []
    seen_ids: set[int] = set()

    def _add(w: Wish) -> None:
        if w.id not in seen_ids:
            seen_ids.add(w.id)
            matched.append(w)

    for wish in open_wishes:
        # Strategy 1: ISBN exact match
        if book.isbn and wish.isbn and book.isbn.strip() == wish.isbn.strip():
            _add(wish)
            continue

        # Strategy 2: Series match (author-aware — disambiguates same-named series
        # like "The Good Guys" by Eric Ugland vs by Bill Bonanno)
        if (
            wish.series
            and book.series
            and wish.series.lower().strip() == book.series.lower().strip()
            and (
                not wish.author
                or not book.author
                or wish.author.lower().strip() == book.author.lower().strip()
            )
        ):
            if wish.series_index is not None:
                # Single-volume wish: only match the exact volume
                if (
                    book.series_index is not None
                    and book.series_index == wish.series_index
                ):
                    _add(wish)
                # If the volume doesn't match, don't add (skip to strategy 3)
            else:
                # Standing whole-series wish: any volume is a candidate.
                # The wish stays open per §12.5 — matcher never changes status.
                _add(wish)
            continue

        # Strategy 3: Fuzzy title + author (SequenceMatcher > 0.85)
        if _fuzzy_match(book, wish):
            _add(wish)

    return matched


def _fuzzy_match(book: "Book", wish: Wish) -> bool:
    """Return True when title similarity > 0.85 and author similarity is close enough."""
    title_ratio = difflib.SequenceMatcher(
        None,
        (book.title or "").lower().strip(),
        (wish.title or "").lower().strip(),
    ).ratio()

    if title_ratio <= _FUZZY_THRESHOLD:
        return False

    # If both have an author, require author similarity > 0.7 to avoid false positives
    if book.author and wish.author:
        author_ratio = difflib.SequenceMatcher(
            None,
            book.author.lower().strip(),
            wish.author.lower().strip(),
        ).ratio()
        if author_ratio < 0.7:
            return False

    return True


def find_matching_books(db: Session, wish: Wish) -> list["Book"]:
    """Return existing books in the library that satisfy the given wish.

    Mirror of find_matching_wishes but in the reverse direction: given a wish,
    scan the existing library for books that would fulfil it.

    Strategies (in priority order):
    1. ISBN exact — if wish.isbn is set, books sharing that ISBN.
    2. Whole-series wish (wish.series set, wish.series_index None) — books
       where Book.series matches case-insensitively, OR Book.title starts with
       the series name (catches books whose series column is empty but whose
       title leads with the series name, e.g. "Mistborn: The Final Empire").
    3. Single-volume series wish (wish.series + wish.series_index both set) —
       books where series and series_index both match exactly.
    4. Standalone fuzzy (no wish.series) — SequenceMatcher > 0.85 on
       title+author; only attempted when strategies 1-3 found nothing to avoid
       unnecessary full-library scans.

    Never mutates the wish or any book. Result is de-duplicated and capped at
    _MAX_BOOK_SUGGESTIONS.
    """
    from backend.models.book import Book
    from backend.services.wishlist import _normalise_series

    matched: list["Book"] = []
    seen_ids: set[int] = set()

    def _add(b: "Book") -> bool:
        """Add book to results if not already present; returns True if added."""
        if b.id not in seen_ids and len(matched) < _MAX_BOOK_SUGGESTIONS:
            seen_ids.add(b.id)
            matched.append(b)
            return True
        return False

    # Strategy 1: ISBN exact
    if wish.isbn:
        isbn_books = (
            db.query(Book)
            .filter(
                Book.status == "active",
                Book.isbn == wish.isbn.strip(),
            )
            .limit(_MAX_BOOK_SUGGESTIONS)
            .all()
        )
        for b in isbn_books:
            _add(b)

    if len(matched) >= _MAX_BOOK_SUGGESTIONS:
        return matched

    # Strategy 2 & 3: Series-based matching
    if wish.series:
        # wish.series is stored post-normalisation (sanitize_name was applied at
        # creation time).  The book's series column may be stored raw.  To match
        # both forms we compare against both the stored wish.series value (e.g.
        # "ReZero") and the raw wish.series value as a fallback — plus, for the
        # title-prefix clause, we also try the raw original so "Mistborn: …"
        # matches a "Mistborn" wish.
        raw_series = wish.series.strip()
        normalised = _normalise_series(raw_series) or raw_series
        # Build the set of lowercase comparison candidates (raw + normalised)
        series_lower_candidates = {raw_series.lower(), normalised.lower()}

        if wish.series_index is None:
            # Whole-series wish: match on series column OR title prefix.
            # (title-prefix catches books where series column is NULL but the
            # title leads with the series name, e.g. "Mistborn: The Final Empire")
            series_clauses = [
                func.lower(Book.series) == s for s in series_lower_candidates
            ] + [
                func.lower(Book.title).like(s + "%") for s in series_lower_candidates
            ]
            series_books = (
                db.query(Book)
                .filter(
                    Book.status == "active",
                    or_(*series_clauses),
                )
                .limit(_MAX_BOOK_SUGGESTIONS)
                .all()
            )
            for b in series_books:
                _add(b)
        else:
            # Single-volume wish: require both series and series_index to match
            series_clauses = [
                func.lower(Book.series) == s for s in series_lower_candidates
            ]
            sv_books = (
                db.query(Book)
                .filter(
                    Book.status == "active",
                    or_(*series_clauses),
                    Book.series_index == wish.series_index,
                )
                .limit(_MAX_BOOK_SUGGESTIONS)
                .all()
            )
            for b in sv_books:
                _add(b)

    if len(matched) >= _MAX_BOOK_SUGGESTIONS:
        return matched

    # Strategy 4: Fuzzy title+author — only for standalone wishes (no series)
    # and only when cheaper strategies found nothing.
    # Scans all active books but caps at _MAX_BOOK_SUGGESTIONS results and
    # bails early once the cap is reached, keeping it O(n) single-pass.
    if not wish.series and not matched:
        all_books = (
            db.query(Book)
            .filter(Book.status == "active")
            .all()
        )
        wish_title = (wish.title or "").lower().strip()
        wish_author = (wish.author or "").lower().strip()

        for b in all_books:
            if len(matched) >= _MAX_BOOK_SUGGESTIONS:
                break
            book_title = (b.title or "").lower().strip()
            title_ratio = difflib.SequenceMatcher(
                None, book_title, wish_title
            ).ratio()
            if title_ratio <= _FUZZY_THRESHOLD:
                continue
            # Author gate: if both sides have an author, require > 0.7 similarity
            if b.author and wish_author:
                author_ratio = difflib.SequenceMatcher(
                    None, b.author.lower().strip(), wish_author
                ).ratio()
                if author_ratio < 0.7:
                    continue
            _add(b)

    return matched


def _notify_volume_available(db: Session, wish: Wish, book: "Book") -> None:
    """In-app notice that a volume of a standing series wish has landed.

    A whole-series wish stays open as volumes arrive (it isn't closed by one
    book), so the requester would otherwise hear nothing until the series is
    marked complete. This fires an "a volume is available" notice on arrival.
    Deduped per (requester, book); failures are swallowed so matching never
    breaks.
    """
    from backend.models.notification import Notification

    link = f"/books/{book.id}"
    try:
        exists = (
            db.query(Notification)
            .filter(
                Notification.user_id == wish.user_id,
                Notification.kind == "wish_volume_available",
                Notification.link == link,
            )
            .first()
        )
        if exists:
            return
        db.add(
            Notification(
                user_id=wish.user_id,
                kind="wish_volume_available",
                title=f"A volume of your \"{wish.series}\" wish is now available",
                body=f"\"{book.title}\" was added to the library.",
                link=link,
            )
        )
    except Exception:
        log.exception(
            "failed to create volume-available notification for wish %d", wish.id
        )


def match_on_book_created(db: Session, book: "Book") -> list[Wish]:
    """Called from every book-creation site after db.add(book) + db.flush().

    Finds matching open wishes and populates their suggested_book_ids.
    Never auto-fulfils — leaves the wishes open for admin confirmation.

    For standing whole-series wishes, also notifies the requester in-app that a
    volume arrived (the wish stays open). Single-book wishes notify on fulfil
    instead, so they're not notified here.

    Returns the list of matched wishes so upload/ingest endpoints can
    surface them in the response.
    """
    try:
        matches = find_matching_wishes(db, book)
        for wish in matches:
            existing_ids: list[int] = []
            if wish.suggested_book_ids:
                try:
                    existing_ids = json.loads(wish.suggested_book_ids)
                except (json.JSONDecodeError, ValueError):
                    existing_ids = []
            if book.id not in existing_ids:
                existing_ids.append(book.id)
            wish.suggested_book_ids = json.dumps(existing_ids)
            log.info(
                "Wish #%d ('%s') matched new book #%d ('%s')",
                wish.id,
                wish.title,
                book.id,
                book.title,
            )

            # Standing whole-series wish (series set, no series_index): notify the
            # requester that a volume arrived. Keeps the wish open.
            if wish.series and wish.series_index is None and wish.status == "open":
                _notify_volume_available(db, wish, book)
        if matches:
            db.flush()
        return matches
    except Exception:
        log.exception(
            "wish_matcher: error matching book #%s — skipping",
            getattr(book, "id", "?"),
        )
        return []
