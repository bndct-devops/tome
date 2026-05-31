"""Wishlist service — business logic for wish creation, fulfilment, and search."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.notification import Notification
from backend.models.user import User
from backend.models.wish import Wish
from backend.schemas.wish import WishCreate
from backend.services.email import send_wish_fulfilled_email
from backend.services.organizer import sanitize_name

log = logging.getLogger(__name__)


def _normalise_series(name: Optional[str]) -> Optional[str]:
    """Normalise a series name using the same sanitiser used on ingest."""
    if not name:
        return name
    return sanitize_name(name.strip()) or name.strip() or None


def create_wish(db: Session, user: User, payload: WishCreate) -> Wish:
    """Create a wish for a user, enforcing cap and uniqueness constraints.

    Raises:
        HTTPException 503 — when wishlist_enabled is False.
        HTTPException 409 — when cap exceeded or duplicate structured wish.
    """
    if not settings.wishlist_enabled:
        raise HTTPException(status_code=503, detail="Wishlist feature is disabled")

    # Soft cap: count open wishes for this user
    open_count = (
        db.query(Wish)
        .filter(Wish.user_id == user.id, Wish.status == "open")
        .count()
    )
    if open_count >= settings.wishlist_max_open_per_user:
        raise HTTPException(
            status_code=409,
            detail=f"Open wish limit ({settings.wishlist_max_open_per_user}) reached",
        )

    # Uniqueness: only enforce for structured (non-null source + source_id) wishes
    if payload.source and payload.source_id:
        existing = (
            db.query(Wish)
            .filter(
                Wish.user_id == user.id,
                Wish.source == payload.source,
                Wish.source_id == payload.source_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="You already have a wish for this item")

    wish = Wish(
        user_id=user.id,
        title=payload.title.strip(),
        author=payload.author,
        series=_normalise_series(payload.series),
        series_index=payload.series_index,
        cover_url=payload.cover_url,
        source=payload.source,
        source_id=payload.source_id,
        isbn=payload.isbn,
        note=payload.note,
        external_series_id=payload.external_series_id,
        latest_known_index=payload.series_total,
        status="open",
        kind="wish",
    )
    db.add(wish)
    db.flush()
    return wish


def fulfill_wish(db: Session, wish: Wish, book: Optional["Book"], admin: User) -> Wish:  # type: ignore[name-defined]
    """Mark a wish as fulfilled, create an in-app notification, attempt email.

    ``book`` is the specific book linked to the wish. It may be ``None`` for a
    whole-series "mark complete" — the requester is still notified, but no single
    volume is linked (a series want is satisfied by the collection, not one book).

    Raises:
        HTTPException 409 — if already fulfilled.
    """
    if wish.status == "fulfilled":
        raise HTTPException(status_code=409, detail="Wish is already fulfilled")

    wish.status = "fulfilled"
    wish.fulfilled_book_id = book.id if book is not None else None
    wish.fulfilled_by = admin.id
    wish.fulfilled_at = datetime.utcnow()
    db.flush()

    # In-app notification (always) — wording differs for a series completion
    if book is not None:
        notif_title = f"Your wish \"{wish.title}\" is now in the library"
        notif_body = f"An admin added \"{book.title}\" — it matches your wish."
        notif_link = f"/books/{book.id}"
    else:
        notif_title = f"Your series wish \"{wish.title}\" is complete"
        notif_body = f"An admin marked your \"{wish.title}\" series wish complete — it's available in the library."
        notif_link = "/wishlist"
    notification = Notification(
        user_id=wish.user_id,
        kind="wish_fulfilled",
        title=notif_title,
        body=notif_body,
        link=notif_link,
    )
    db.add(notification)
    db.flush()

    # Email notification (only when SMTP configured, failures swallowed)
    if settings.smtp_configured:
        try:
            requester = db.get(User, wish.user_id)
            if requester and requester.email:
                send_wish_fulfilled_email(requester.email, wish, book)
        except Exception:
            log.exception("Failed to send wish-fulfilled email for wish %d", wish.id)

    return wish


# Coverage surfaces every matching volume in the library, not the matcher's
# suggestion cap — a long series can have many volumes present at once.
_COVERAGE_CAP = 100


def series_coverage(db: Session, wish: Wish) -> list:
    """For a whole-series wish, return the volumes currently in the library that
    match it, sorted by series_index. Empty for single-book or single-volume
    wishes (coverage only makes sense for a standing whole-series want).

    Uses its own query (not the matcher's capped suggestion list) so all present
    volumes are surfaced — e.g. a 16-volume series shows all 16.
    """
    if not (wish.series and wish.series_index is None):
        return []
    from sqlalchemy import func, or_
    from backend.models.book import Book

    raw = wish.series.strip()
    normalised = _normalise_series(raw) or raw
    candidates = {raw.lower(), normalised.lower()}
    clauses = [func.lower(Book.series) == s for s in candidates] + [
        func.lower(Book.title).like(s + "%") for s in candidates
    ]
    q = db.query(Book).filter(Book.status == "active", or_(*clauses))
    # Disambiguate same-named series (e.g. "The Good Guys" by Eric Ugland vs by
    # Bill Bonanno): when the wish carries an author, only count that author's books.
    if wish.author:
        q = q.filter(func.lower(Book.author) == wish.author.lower().strip())
    books = q.limit(_COVERAGE_CAP).all()
    books.sort(key=lambda b: (b.series_index is None, b.series_index or 0.0, b.title or ""))
    return books


async def search_candidates(q: str) -> list:
    """Thin wrapper over metadata_fetch.fetch_candidates for wishlist search."""
    from backend.services.metadata_fetch import fetch_candidates as _fetch
    result = await _fetch(title=q, query_override=None)
    return result.candidates
