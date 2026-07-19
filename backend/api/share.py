"""Public share links for shelves — metadata only, by construction.

THE BOUNDARY (owner's explicit constraint, do not loosen): a share exposes
cover, title, author/series identity, tags, description, the owner's rating,
and their highlights. Nothing else. No file paths, no download routes, no
reader, no route from a share to any book content, ever.

That boundary is structural: this module imports nothing from the file-serving
side (downloads, metadata_embed, book files), and the public endpoint builds
its response through an explicit whitelist serializer — a field that isn't
listed here cannot leak. The only binary a share page touches is the cover
endpoint, which is already public. Tokens are 128-bit random and revocable;
responses carry X-Robots-Tag: noindex.
"""
from __future__ import annotations

import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.library import SavedFilter, ShareLink
from backend.models.tome_sync import Annotation
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.audit import audit
from backend.services.shelf_resolver import shelf_query

log = logging.getLogger(__name__)
router = APIRouter(tags=["share"])


def _own_shelf_or_404(db: Session, user: User, shelf_id: int) -> SavedFilter:
    sf = db.get(SavedFilter, shelf_id)
    if not sf or sf.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Shelf not found")
    return sf


# ── Management (authenticated, owner only) ────────────────────────────────────

def _get_or_create(db: Session, user: User, *, resource_type: str,
                   resource_title: str, **target) -> ShareLink:
    link = db.query(ShareLink).filter_by(owner_id=user.id, **target).first()
    if link is None:
        link = ShareLink(token=secrets.token_urlsafe(16), owner_id=user.id, **target)
        db.add(link)
        db.commit()
        db.refresh(link)
        audit(db, "share_link.created", user_id=user.id, username=user.username,
              resource_type=resource_type, resource_title=resource_title)
    return link


def _revoke(db: Session, user: User, *, resource_type: str,
            resource_title: str, **target) -> None:
    link = db.query(ShareLink).filter_by(owner_id=user.id, **target).first()
    if link:
        db.delete(link)
        db.commit()
        audit(db, "share_link.revoked", user_id=user.id, username=user.username,
              resource_type=resource_type, resource_title=resource_title)

@router.get("/shelves/{shelf_id}/share")
def get_share(
    shelf_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _own_shelf_or_404(db, current_user, shelf_id)
    link = db.query(ShareLink).filter(ShareLink.saved_filter_id == shelf_id).first()
    return {"token": link.token if link else None}


@router.post("/shelves/{shelf_id}/share", status_code=201)
def create_share(
    shelf_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    sf = _own_shelf_or_404(db, current_user, shelf_id)
    link = _get_or_create(db, current_user, resource_type="shelf",
                          resource_title=sf.name, saved_filter_id=shelf_id)
    return {"token": link.token}


@router.delete("/shelves/{shelf_id}/share")
def revoke_share(
    shelf_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    sf = _own_shelf_or_404(db, current_user, shelf_id)
    _revoke(db, current_user, resource_type="shelf",
            resource_title=sf.name, saved_filter_id=shelf_id)
    return {"ok": True}


# Series shares: the name is the identity (matches the /series/{name}/meta
# convention). One link per (owner, series).

@router.get("/series/{name}/share")
def get_series_share(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    link = db.query(ShareLink).filter_by(owner_id=current_user.id, series_name=name).first()
    return {"token": link.token if link else None}


@router.post("/series/{name}/share", status_code=201)
def create_series_share(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from backend.models.book import Book
    from backend.core.permissions import book_visibility_filter
    exists = db.query(Book.id).filter(
        Book.status == "active", Book.series == name,
        book_visibility_filter(db, current_user),
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Series not found")
    link = _get_or_create(db, current_user, resource_type="series",
                          resource_title=name, series_name=name)
    return {"token": link.token}


@router.delete("/series/{name}/share")
def revoke_series_share(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _revoke(db, current_user, resource_type="series",
            resource_title=name, series_name=name)
    return {"ok": True}


# Single-book shares. One link per (owner, book).

@router.get("/books/{book_id}/share")
def get_book_share(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    link = db.query(ShareLink).filter_by(owner_id=current_user.id, book_id=book_id).first()
    return {"token": link.token if link else None}


@router.post("/books/{book_id}/share", status_code=201)
def create_book_share(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from backend.models.book import Book
    from backend.core.permissions import user_can_see_book
    book = db.get(Book, book_id)
    if not book or book.status != "active" or not user_can_see_book(db, current_user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    link = _get_or_create(db, current_user, resource_type="book",
                          resource_title=book.title, book_id=book_id)
    return {"token": link.token}


@router.delete("/books/{book_id}/share")
def revoke_book_share(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from backend.models.book import Book
    book = db.get(Book, book_id)
    _revoke(db, current_user, resource_type="book",
            resource_title=book.title if book else str(book_id), book_id=book_id)
    return {"ok": True}


# ── Public view (no auth — the token IS the capability) ───────────────────────

@router.get("/share/{token}")
def public_share(
    token: str,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    owner = db.get(User, link.owner_id) if link else None
    if link is None or owner is None:
        raise HTTPException(status_code=404, detail="This share link does not exist (or was revoked)")

    from backend.models.book import Book
    from backend.core.permissions import book_visibility_filter, user_can_see_book

    if link.saved_filter_id is not None:
        sf = db.get(SavedFilter, link.saved_filter_id)
        if sf is None:
            raise HTTPException(status_code=404, detail="This share link does not exist (or was revoked)")
        try:
            params = json.loads(sf.params or "{}")
        except ValueError:
            params = {}
        # Visibility is the OWNER's — a share can never show more than they see.
        query, _unsupported = shelf_query(db, owner, params)
        kind, title = "shelf", sf.name
    elif link.series_name is not None:
        query = db.query(Book).filter(
            Book.status == "active", Book.series == link.series_name,
            book_visibility_filter(db, owner),
        )
        kind, title = "series", link.series_name
    else:
        book = db.get(Book, link.book_id) if link.book_id else None
        if not book or book.status != "active" or not user_can_see_book(db, owner, book):
            raise HTTPException(status_code=404, detail="This share link does not exist (or was revoked)")
        serialized = _serialize_books(db, owner, [book])
        return {
            "kind": "book",
            "title": book.title,
            "totals": {
                "books": 1,
                "read": sum(1 for b in serialized if (b["stats"] or {}).get("status") == "read"),
                "total_seconds": sum((b["stats"] or {}).get("total_seconds", 0) for b in serialized),
            },
            "books": serialized,
        }

    books = (
        query.order_by(Book.series.asc().nullslast(),
                       Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )
    seen: set[int] = set()
    unique = [b for b in books if not (b.id in seen or seen.add(b.id))]
    serialized = _serialize_books(db, owner, unique)
    return {
        "kind": kind,
        "title": title,
        "totals": {
            "books": len(serialized),
            "read": sum(1 for b in serialized if (b["stats"] or {}).get("status") == "read"),
            "total_seconds": sum((b["stats"] or {}).get("total_seconds", 0) for b in serialized),
        },
        "books": serialized,
    }


def _serialize_books(db: Session, owner: User, books: list) -> list[dict]:
    """THE whitelist. Every public payload goes through here; a field that
    isn't listed cannot leak.

    Includes the owner's reading stats for each book (time, days, status,
    finish date, per-day activity) — that is the owner's own data, not book
    content, and it is what makes a share worth looking at. Still zero routes
    to files."""
    from backend.services import reconciled_reading as rr
    from backend.services.reading_day import date_modifier

    book_ids = [b.id for b in books]
    status_rows = {
        s.book_id: s
        for s in db.query(UserBookStatus).filter(
            UserBookStatus.user_id == owner.id,
            UserBookStatus.book_id.in_(book_ids or [0]),
        )
    }
    ratings = {bid: s.rating for bid, s in status_rows.items() if s.rating is not None}

    # Reconciled per-day reading (UTC day-bucketing — the owner's exact tz is
    # unknown server-side and a public page doesn't need it to the hour).
    covered = rr.covered_book_ids(db, owner.id)
    day_secs = rr.book_day_seconds(db, owner.id, date_modifier(0), covered)
    activity: dict[int, list[dict]] = {}
    for (bid, day), secs in sorted(day_secs.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        if bid in (set(book_ids)) and secs > 0 and day is not None:
            activity.setdefault(bid, []).append({"date": day, "seconds": secs})

    def _stats(bid: int) -> dict | None:
        days = activity.get(bid, [])
        srow = status_rows.get(bid)
        status = srow.status if srow and srow.status in ("reading", "read") else None
        if not days and not status:
            return None
        return {
            "status": status,
            "total_seconds": sum(d["seconds"] for d in days),
            "reading_days": len(days),
            "first_day": days[0]["date"] if days else None,
            "last_day": days[-1]["date"] if days else None,
            "finished_on": (srow.finished_at.date().isoformat()
                            if srow and srow.finished_at else None),
            "activity": days,
        }
    highlights: dict[int, list[dict]] = {}
    for a in db.query(Annotation).filter(
        Annotation.user_id == owner.id,
        Annotation.book_id.in_(book_ids or [0]),
    ).order_by(Annotation.id):
        highlights.setdefault(a.book_id, []).append({
            # Whitelist within the whitelist: quote text, note, chapter. No
            # anchors, no device metadata.
            "text": a.highlighted_text,
            "note": a.note,
            "chapter": a.chapter,
        })
    return [
        {
            # book id is needed solely for the (already-public) cover
            # endpoint; everything below is the complete public surface.
            "id": b.id,
            "title": b.title,
            "author": b.author,
            "series": b.series,
            "series_index": b.series_index,
            "description": b.description,
            "tags": sorted({t.tag for t in b.tags}),
            "rating": ratings.get(b.id),
            "stats": _stats(b.id),
            "highlights": highlights.get(b.id, []),
        }
        for b in books
    ]
