"""Wishlist API — member and admin endpoints.

Mounted at /api, tags=["wishlist"].
All routes accept JWT or tome_* tokens (universal auth via get_current_user).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import require_role
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.notification import Notification
from backend.models.user import User
from backend.models.wish import Wish
from backend.schemas.wish import (
    FulfillRequest,
    WishAdminOut,
    WishCoverageVolume,
    WishCreate,
    WishOut,
    WishSearchResult,
    WishSeriesResult,
)
from backend.services.audit import audit
from backend.services.wishlist import create_wish, fulfill_wish, series_coverage

log = logging.getLogger(__name__)

router = APIRouter(tags=["wishlist"])


# ── Member endpoints ──────────────────────────────────────────────────────────

@router.get("/wishlist", response_model=list[WishOut])
def list_wishes(
    status: Optional[str] = Query(None, description="Filter by status: open|fulfilled|dismissed"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the current user's wishes (kind="wish" — follows live at /wishlist/follows)."""
    require_role(current_user, "member")

    q = db.query(Wish).filter(Wish.user_id == current_user.id, Wish.kind == "wish")
    if status:
        q = q.filter(Wish.status == status)
    wishes = q.order_by(Wish.created_at.desc()).all()

    result: list[WishOut] = []
    for w in wishes:
        out = WishOut.model_validate(w)
        # Whole-series wishes: show which volumes are already in the library.
        if w.status == "open" and w.series and w.series_index is None:
            out.series_coverage = [
                WishCoverageVolume.model_validate(b) for b in series_coverage(db, w)
            ]
            out.series_total = int(w.latest_known_index) if w.latest_known_index else None
        result.append(out)
    return result


@router.post("/wishlist", response_model=WishOut, status_code=201)
def add_wish(
    payload: WishCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new wish for the current user."""
    require_role(current_user, "member")

    wish = create_wish(db, current_user, payload)
    db.commit()
    db.refresh(wish)
    return wish


@router.delete("/wishlist/{wish_id}", status_code=204)
def delete_wish(
    wish_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a wish (owner only)."""
    require_role(current_user, "member")

    wish = db.get(Wish, wish_id)
    if not wish:
        raise HTTPException(status_code=404, detail="Wish not found")
    if wish.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Wish not found")

    db.delete(wish)
    db.commit()


@router.get("/wishlist/search", response_model=list[WishSearchResult])
async def search_wishes(
    q: str = Query(..., description="Search query"),
    _: User = Depends(get_current_user),
):
    """Proxy to metadata_fetch.fetch_candidates for wishlist item search."""
    from backend.services.wishlist import search_candidates
    candidates = await search_candidates(q)
    return [
        WishSearchResult(
            source=c.source,
            source_id=c.source_id,
            title=c.title,
            author=c.author,
            cover_url=c.cover_url,
            series=c.series,
            series_index=c.series_index,
            isbn=c.isbn,
            year=c.year,
            description=c.description,
        )
        for c in candidates
    ]


@router.get("/wishlist/search-series", response_model=list[WishSeriesResult])
async def search_wish_series(
    q: str = Query(..., description="Series search query"),
    _: User = Depends(get_current_user),
):
    """Search Hardcover for SERIES entities — for whole-series wishes. Returns a
    canonical series id, author, and the true volume count (so two same-named
    series are distinguishable and coverage can show 'X of N')."""
    from backend.services.metadata_fetch import search_series
    rows = await search_series(q)
    return [WishSeriesResult(**r) for r in rows]


@router.get("/wishlist/series-search-available")
def series_search_available(_: User = Depends(get_current_user)) -> dict:
    """Series search is Hardcover-only — the add dialog hides the Series tab when
    no Hardcover token is configured (book search still works via Google/OL)."""
    return {"available": bool(settings.hardcover_token)}


# ── Follows (release detection) ──────────────────────────────────────────────
# A follow is a Wish with kind="follow": the columns reserved by the wishlist
# plan (external_series_id / last_checked_at / latest_known_index) come alive.
# Gated by TOME_RELEASE_DETECTION; the poller lives in services/release_detection.

from pydantic import BaseModel as _BaseModel


class FollowCreate(_BaseModel):
    name: str                      # series name (display + library matching)
    source_id: Optional[str] = None  # Hardcover series id; resolved by name if absent
    author: Optional[str] = None
    cover_url: Optional[str] = None


def _require_detection():
    if not settings.release_detection:
        raise HTTPException(status_code=403, detail="Release detection is disabled (TOME_RELEASE_DETECTION)")


def _follow_out(db: Session, current_user: User, w: Wish) -> dict:
    # Highest volume of this series already in the library (visibility-gated).
    from sqlalchemy import func as _f
    from backend.core.permissions import book_visibility_filter
    owned_q = db.query(_f.max(Book.series_index)).filter(
        Book.series == w.series, Book.status == "active",
    )
    vis = book_visibility_filter(db, current_user)
    if vis is not True:
        owned_q = owned_q.filter(vis)
    owned = owned_q.scalar()
    return {
        "id": w.id,
        "name": w.title,
        "author": w.author,
        "cover_url": w.cover_url,
        "source_id": w.source_id,
        "latest_known_index": w.latest_known_index,
        "latest_known_title": w.latest_known_title,
        "latest_release_date": w.latest_release_date,
        "owned_max_index": float(owned) if owned is not None else None,
        "last_checked_at": w.last_checked_at.isoformat() + "Z" if w.last_checked_at else None,
        "created_at": w.created_at.isoformat() + "Z" if w.created_at else None,
    }


@router.get("/wishlist/follows")
def list_follows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The current user's followed series, with the tracker vs library state."""
    require_role(current_user, "member")
    _require_detection()
    rows = (
        db.query(Wish)
        .filter(Wish.user_id == current_user.id, Wish.kind == "follow", Wish.status == "open")
        .order_by(Wish.created_at.desc())
        .all()
    )
    return [_follow_out(db, current_user, w) for w in rows]


@router.post("/wishlist/follow", status_code=201)
async def follow_series(
    payload: FollowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Follow a series: resolve it on Hardcover, prime the release watermark.

    Priming means only releases AFTER following notify — following a
    27-volume series doesn't fire 27 alerts.
    """
    require_role(current_user, "member")
    _require_detection()
    from backend.services.metadata_fetch import search_series
    from backend.services.release_detection import fetch_series_latest
    from datetime import datetime as _dt

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")

    sid = payload.source_id
    author, cover = payload.author, payload.cover_url
    if not sid:
        hits = await search_series(name)
        if not hits:
            raise HTTPException(status_code=404, detail="Series not found on Hardcover")
        best = hits[0]
        sid, name = best["source_id"], best["name"]
        author = author or best.get("author")
        cover = cover or best.get("cover_url")

    dup = (
        db.query(Wish)
        .filter(Wish.user_id == current_user.id, Wish.kind == "follow",
                Wish.external_series_id == str(sid), Wish.status == "open")
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail="Already following this series")

    wish = Wish(
        user_id=current_user.id, kind="follow", status="open",
        title=name, series=name, author=author, cover_url=cover,
        source="hardcover", source_id=f"series:{sid}",
        external_series_id=str(sid),
    )
    db.add(wish)
    db.flush()

    state = await fetch_series_latest(int(sid))
    if state:
        wish.latest_known_index = state["latest_index"]
        wish.latest_known_title = state.get("latest_title")
        wish.latest_release_date = state.get("release_date")
        wish.last_checked_at = _dt.utcnow()

    db.commit()
    db.refresh(wish)
    audit(
        db,
        "wishlist.follow",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="wish",
        resource_id=wish.id,
        details={"series": name, "hardcover_id": str(sid)},
    )
    return _follow_out(db, current_user, wish)


@router.post("/admin/release-check")
async def admin_release_check(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the release-detection poll for every open follow, right now."""
    require_role(current_user, "admin")
    _require_detection()
    from backend.services.release_detection import check_follows
    return await check_follows(db, force=True)


# ── Admin endpoints ───────────────────────────────────────────────────────────

def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    require_role(current_user, "admin")
    return current_user


@router.get("/admin/wishlist", response_model=list[WishAdminOut])
def admin_list_wishes(
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    """Admin view: all wishes across all members, joined with requester username."""
    from backend.models.user import User as _User

    q = db.query(Wish)
    if status:
        q = q.filter(Wish.status == status)
    if user_id:
        q = q.filter(Wish.user_id == user_id)

    wishes = q.order_by(Wish.created_at.desc()).all()

    # Build requester username map in one query
    user_ids = {w.user_id for w in wishes}
    user_map: dict[int, str] = {}
    if user_ids:
        rows = db.query(_User.id, _User.username).filter(_User.id.in_(user_ids)).all()
        user_map = {row[0]: row[1] for row in rows}

    from backend.services.wish_matcher import find_matching_books

    result: list[WishAdminOut] = []
    for w in wishes:
        out = WishAdminOut.model_validate(w)
        out.requester_username = user_map.get(w.user_id)

        # For open wishes, augment stored suggested_book_ids with a live reverse
        # match against the current library.  This catches the "wish created
        # after the book was already in the library" case where the forward
        # matcher never fired.  We do NOT persist these live matches — they are
        # computed at read time only.
        if w.status == "open":
            stored_ids: list[int] = out.suggested_book_ids or []
            live_books = find_matching_books(db, w)
            live_ids = [b.id for b in live_books]
            # Union: stored first (preserves order), then new live hits, de-duped
            seen: set[int] = set(stored_ids)
            merged: list[int] = list(stored_ids)
            for bid in live_ids:
                if bid not in seen:
                    seen.add(bid)
                    merged.append(bid)
            # Cap to 10 to avoid an unbounded list in the response
            out.suggested_book_ids = merged[:10] if merged else None

            # Whole-series wish: surface every volume already in the library
            # (coverage uses its own uncapped query, not the 10-item suggestion).
            if w.series and w.series_index is None:
                out.series_coverage = [
                    WishCoverageVolume.model_validate(b) for b in series_coverage(db, w)
                ]
                out.series_total = int(w.latest_known_index) if w.latest_known_index else None

        result.append(out)

    return result


@router.get("/admin/wishlist/matches", response_model=list[WishAdminOut])
def admin_wish_matches(
    book_id: int = Query(..., description="Book to match against open wishes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    """Return open wishes that match the given book (used post-upload prompt)."""
    from backend.services.wish_matcher import find_matching_wishes
    from backend.models.user import User as _User

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    wishes = find_matching_wishes(db, book)

    user_ids = {w.user_id for w in wishes}
    user_map: dict[int, str] = {}
    if user_ids:
        rows = db.query(_User.id, _User.username).filter(_User.id.in_(user_ids)).all()
        user_map = {row[0]: row[1] for row in rows}

    result: list[WishAdminOut] = []
    for w in wishes:
        out = WishAdminOut.model_validate(w)
        out.requester_username = user_map.get(w.user_id)
        result.append(out)

    return result


@router.post("/admin/wishlist/{wish_id}/fulfill", response_model=WishOut)
def admin_fulfill_wish(
    wish_id: int,
    body: FulfillRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    """Fulfill a wish.

    Single-book wishes require a ``book_id`` to link. A whole-series wish
    (``series`` set, no ``series_index``) is a standing want — it is closed via
    "mark complete" with no ``book_id``, linking no single volume.
    """
    wish = db.get(Wish, wish_id)
    if not wish:
        raise HTTPException(status_code=404, detail="Wish not found")

    is_whole_series = bool(wish.series) and wish.series_index is None

    book = None
    if body.book_id is not None:
        book = db.get(Book, body.book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
    elif not is_whole_series:
        raise HTTPException(status_code=400, detail="book_id is required to fulfill this wish")

    wish = fulfill_wish(db, wish, book, current_user)

    audit(
        db,
        "wishlist.fulfilled",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="wish",
        resource_id=wish.id,
        resource_title=wish.title,
        details={
            "requester_user_id": wish.user_id,
            "book_id": book.id if book is not None else None,
        },
    )

    db.commit()
    db.refresh(wish)
    return wish


@router.post("/admin/wishlist/{wish_id}/dismiss", response_model=WishOut)
def admin_dismiss_wish(
    wish_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    """Dismiss a wish without fulfilling it. Creates in-app notification (no email)."""
    wish = db.get(Wish, wish_id)
    if not wish:
        raise HTTPException(status_code=404, detail="Wish not found")

    if wish.status not in ("open",):
        raise HTTPException(status_code=409, detail=f"Cannot dismiss a wish with status '{wish.status}'")

    wish.status = "dismissed"
    db.flush()

    # In-app notification
    notification = Notification(
        user_id=wish.user_id,
        kind="wish_dismissed",
        title=f"Your wish \"{wish.title}\" was closed",
        body="An admin reviewed your wish and closed it without fulfilling.",
        link="/wishlist",
    )
    db.add(notification)

    audit(
        db,
        "wishlist.dismissed",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="wish",
        resource_id=wish.id,
        resource_title=wish.title,
        details={"requester_user_id": wish.user_id},
    )

    db.commit()
    db.refresh(wish)
    return wish
