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
    link = db.query(ShareLink).filter(ShareLink.saved_filter_id == shelf_id).first()
    if link is None:
        link = ShareLink(
            token=secrets.token_urlsafe(16),
            saved_filter_id=shelf_id,
            owner_id=current_user.id,
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        audit(db, "share_link.created", user_id=current_user.id,
              username=current_user.username, resource_type="shelf",
              resource_id=shelf_id, resource_title=sf.name)
    return {"token": link.token}


@router.delete("/shelves/{shelf_id}/share")
def revoke_share(
    shelf_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    sf = _own_shelf_or_404(db, current_user, shelf_id)
    link = db.query(ShareLink).filter(ShareLink.saved_filter_id == shelf_id).first()
    if link:
        db.delete(link)
        db.commit()
        audit(db, "share_link.revoked", user_id=current_user.id,
              username=current_user.username, resource_type="shelf",
              resource_id=shelf_id, resource_title=sf.name)
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
    if link is None:
        raise HTTPException(status_code=404, detail="This share link does not exist (or was revoked)")
    sf = db.get(SavedFilter, link.saved_filter_id)
    owner = db.get(User, link.owner_id)
    if sf is None or owner is None:
        raise HTTPException(status_code=404, detail="This share link does not exist (or was revoked)")

    try:
        params = json.loads(sf.params or "{}")
    except ValueError:
        params = {}
    # Visibility is the OWNER's — a share can never show more than they can see.
    query, _unsupported = shelf_query(db, owner, params)
    from backend.models.book import Book
    books = (
        query.order_by(Book.series.asc().nullslast(),
                       Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )
    seen: set[int] = set()
    unique = [b for b in books if not (b.id in seen or seen.add(b.id))]
    book_ids = [b.id for b in unique]

    ratings = {
        s.book_id: s.rating
        for s in db.query(UserBookStatus).filter(
            UserBookStatus.user_id == owner.id,
            UserBookStatus.book_id.in_(book_ids or [0]),
            UserBookStatus.rating.isnot(None),
        )
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

    return {
        "shelf": sf.name,
        "books": [
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
                "highlights": highlights.get(b.id, []),
            }
            for b in unique
        ],
    }
