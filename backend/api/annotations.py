"""Annotations API — the cross-library Highlights / commonplace view.

`GET /api/annotations` returns the current user's KOReader-synced highlights
across *all* visible books (the per-book view lives at
`GET /api/books/{id}/annotations`). The web's writes:

- `POST /api/annotations` — create a highlight from the web reader. It gets a
  provisional `web:<uuid>` anchor plus the selection's CFI; a KOReader device
  later "adopts" it (locates the text, re-anchors it natively) via the sync
  endpoint, which retires the provisional row.
- `PUT /api/annotations/{id}` — edit note/colour; bumps the LWW mtime so the
  change propagates to devices like any device-side edit.
- `DELETE /api/annotations/{id}` — drops the row and leaves a tombstone so the
  deletion propagates back to devices (and stale devices can't resurrect it).

Registered before `/api/books/{id}` is irrelevant here (distinct prefix), but the
router is mounted at /api like the rest.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import book_visibility_filter, user_can_see_book
from backend.models.book import Book
from backend.models.tome_sync import Annotation, AnnotationTombstone
from backend.models.user import User
from backend.services.audit import audit

router = APIRouter(tags=["annotations"])


def _to_item(a, b) -> dict:
    """Shape one (Annotation, Book) row for the API, with book context."""
    return {
        "id": a.id,
        "book_id": b.id,
        "book_title": b.title,
        "book_author": b.author,
        "book_cover": f"/api/books/{b.id}/cover" if b.cover_path else None,
        "highlighted_text": a.highlighted_text,
        "note": a.note,
        "chapter": a.chapter,
        "color": a.color,
        "datetime": a.koreader_datetime,  # when highlighted on the device
        "synced_at": a.created_at.isoformat() + "Z",  # when Tome first stored it
    }


def _month_day(dt: Optional[str]) -> Optional[str]:
    """Pull 'MM-DD' out of a KOReader datetime string like '2024-01-15 10:30:00'.

    KOReader writes 'YYYY-MM-DD HH:MM:SS' (local wall-clock). We only need the
    month-day for the 'on this day' filter, so a cheap slice is enough; return
    None for anything that doesn't look like a date.
    """
    if not dt or len(dt) < 10 or dt[4] != "-" or dt[7] != "-":
        return None
    return dt[5:10]


@router.get("/annotations")
def list_annotations(
    q: Optional[str] = Query(None, description="case-insensitive text search"),
    on_this_day: bool = Query(False),
    only_notes: bool = Query(False, description="only highlights carrying a note"),
    # 10k ceiling: the export paths ask for everything at once, and a request
    # over the cap is a 422, not a truncation — see the Markdown export.
    limit: int = Query(200, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The current user's highlights across every book they can see."""
    query = (
        db.query(Annotation, Book)
        .join(Book, Annotation.book_id == Book.id)
        .filter(
            Annotation.user_id == current_user.id,
            Book.status == "active",
            book_visibility_filter(db, current_user),
        )
    )

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Annotation.highlighted_text.ilike(like),
                Annotation.note.ilike(like),
                Annotation.chapter.ilike(like),
                Book.title.ilike(like),
            )
        )

    if only_notes:
        query = query.filter(Annotation.note.isnot(None), Annotation.note != "")

    # Newest highlights first (KOReader's own timestamp, falling back to id).
    rows = (
        query.order_by(Annotation.koreader_datetime.desc().nullslast(), Annotation.id.desc())
        .all()
    )

    if on_this_day:
        today = _month_day(func_now_str())
        rows = [(a, b) for (a, b) in rows if _month_day(a.koreader_datetime) == today]

    total = len(rows)
    book_ids = {b.id for (_, b) in rows}
    page = rows[offset : offset + limit]

    items = [_to_item(a, b) for (a, b) in page]

    return {"total": total, "books": len(book_ids), "items": items}


class AnnotationCreate(BaseModel):
    book_id: int
    highlighted_text: str
    cfi: Optional[str] = None
    note: Optional[str] = None
    chapter: Optional[str] = None
    color: Optional[str] = None
    datetime: Optional[str] = None   # client wall-clock "YYYY-MM-DD HH:MM:SS"


class AnnotationEdit(BaseModel):
    note: Optional[str] = None
    color: Optional[str] = None


def _annotation_out(a: Annotation) -> dict:
    return {
        "id": a.id,
        "book_id": a.book_id,
        "anchor": a.anchor,
        "cfi": a.cfi,
        "highlighted_text": a.highlighted_text,
        "note": a.note,
        "chapter": a.chapter,
        "color": a.color,
        "datetime": a.koreader_datetime,
        "datetime_updated": a.koreader_datetime_updated,
    }


@router.post("/annotations", status_code=status.HTTP_201_CREATED)
def create_annotation(
    payload: AnnotationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a highlight from the web reader.

    Stored under a provisional ``web:<uuid>`` anchor with the selection's CFI
    (so the web can re-paint it directly). A KOReader device adopts it on its
    next sync: it locates the text, creates a native annotation with a real
    xPointer, and the sync endpoint retires this provisional row. Until then it
    lives on the web (and in the Highlights views) like any other highlight.
    """
    book = db.get(Book, payload.book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")
    if not user_can_see_book(db, current_user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    text_ = (payload.highlighted_text or "").strip()
    if not text_:
        raise HTTPException(status_code=422, detail="highlighted_text must not be empty")
    if len(text_) > 20_000:
        raise HTTPException(status_code=422, detail="highlighted_text too long")

    client_dt = (payload.datetime or "").strip()[:19]
    now = client_dt or func_now_str()
    row = Annotation(
        user_id=current_user.id,
        book_id=payload.book_id,
        anchor=f"web:{uuid.uuid4()}",
        cfi=payload.cfi,
        highlighted_text=text_,
        note=(payload.note or None),
        chapter=(payload.chapter or None),
        color=(payload.color or None),
        koreader_datetime=now,
        koreader_datetime_updated=now,
        # Stamp origin: only a server-clock fallback is "server-minted" —
        # plugin syncs shift such stamps into the device's clock frame.
        server_minted=not client_dt,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _annotation_out(row)


@router.put("/annotations/{annotation_id}")
def edit_annotation(
    annotation_id: int,
    payload: AnnotationEdit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit a highlight's note/colour from the web.

    Applies to web-created AND device-synced highlights: the LWW mtime is
    bumped past the row's current one, so the edit wins on every device at its
    next sync — exactly like an edit made on another device.
    """
    row = (
        db.query(Annotation)
        .filter(Annotation.id == annotation_id, Annotation.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Highlight not found")

    raw = payload.model_dump(exclude_unset=True)
    if "note" in raw:
        row.note = payload.note or None
    if "color" in raw:
        row.color = payload.color or None

    # The edit must be STRICTLY newer than the current mtime to win LWW on
    # devices; guard against a server clock at/behind the device's wall-clock.
    new_mtime = func_now_str()
    minted = True
    if new_mtime <= (row.effective_mtime or ""):
        from datetime import datetime as _dt, timedelta as _td
        try:
            base = _dt.strptime(row.effective_mtime, "%Y-%m-%d %H:%M:%S")
            new_mtime = (base + _td(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
            # Bumped off the row's existing mtime, so the new stamp lives in
            # whatever frame that stamp was in (device unless it was itself
            # server-minted) — inherit, don't assume server frame.
            minted = row.server_minted
        except ValueError:
            pass
    row.koreader_datetime_updated = new_mtime
    row.server_minted = minted
    db.commit()
    db.refresh(row)
    return _annotation_out(row)


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(
    annotation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one of the current user's highlights (e.g. an accidental one).

    Mirrors the TomeSync delete path: we remove the `Annotation` row *and* write an
    `AnnotationTombstone` stamped no earlier than the highlight's own mtime. On its next sync
    the device sees the tombstone, finds its local copy older, and drops it too — so
    the deletion sticks instead of being re-uploaded. A later, genuinely-newer re-add
    of the same passage (strictly newer mtime) still wins and clears the tombstone,
    exactly as it would for a device-originated delete.
    """
    annotation = (
        db.query(Annotation)
        .filter(Annotation.id == annotation_id, Annotation.user_id == current_user.id)
        .first()
    )
    if annotation is None:
        raise HTTPException(status_code=404, detail="Highlight not found")

    book_id = annotation.book_id
    anchor = annotation.anchor
    # LWW key: at least the highlight's own device-local mtime, not just the server
    # clock. The server may sit hours behind the device's wall-clock (UTC container vs
    # local-time device), and both tombstone checks (server upsert and plugin apply)
    # drop copies with mtime <= tombstone — so maxing with effective_mtime guarantees
    # the delete holds against the exact copy that was deleted, while a genuinely
    # newer re-add (strictly greater mtime) still wins and clears the tombstone.
    server_now = func_now_str()
    now = max(server_now, annotation.effective_mtime)
    # The stamp is only "server-minted" (shifted into device frames on plugin
    # sync) when the server clock actually won the max — if the highlight's own
    # device mtime won, the stamp is already in that device's frame.
    minted = now == server_now and now != annotation.effective_mtime

    db.delete(annotation)

    tomb = (
        db.query(AnnotationTombstone)
        .filter(
            AnnotationTombstone.user_id == current_user.id,
            AnnotationTombstone.book_id == book_id,
            AnnotationTombstone.anchor == anchor,
        )
        .first()
    )
    if tomb:
        # Keep the latest deletion time so a stale re-add can't slip under it.
        if now > (tomb.client_deleted_at or ""):
            tomb.client_deleted_at = now
            tomb.server_minted = minted
    else:
        db.add(AnnotationTombstone(
            user_id=current_user.id, book_id=book_id, anchor=anchor,
            client_deleted_at=now, server_minted=minted,
        ))

    db.commit()
    audit(db, "annotation.deleted", user_id=current_user.id, username=current_user.username,
          resource_type="annotation", resource_id=annotation_id,
          details={"book_id": book_id})


@router.get("/annotations/spotlight")
def annotation_spotlight(
    exclude: Optional[int] = Query(None, description="annotation id to re-roll away from"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One highlight for the Home tab: a random highlight made on this calendar
    day in a past year, falling back to any random highlight so the card is never
    empty. `on_this_day` tells the UI which case it got.

    `exclude` powers the shuffle button: pass the current highlight's id and the
    re-roll returns a different one whenever more than one candidate exists (the
    on-this-day preference is kept — only its current pick is excluded).
    """
    base = (
        db.query(Annotation, Book)
        .join(Book, Annotation.book_id == Book.id)
        .filter(
            Annotation.user_id == current_user.id,
            Book.status == "active",
            Annotation.highlighted_text.isnot(None),
            Annotation.highlighted_text != "",
            book_visibility_filter(db, current_user),
        )
    )
    if exclude is not None:
        base = base.filter(Annotation.id != exclude)

    today_md = _month_day(func_now_str())
    on_day_row = None
    if today_md:
        on_day_row = (
            base.filter(
                Annotation.koreader_datetime.isnot(None),
                func.substr(Annotation.koreader_datetime, 6, 5) == today_md,
            )
            .order_by(func.random())
            .first()
        )

    row = on_day_row or base.order_by(func.random()).first()
    if not row and exclude is not None:
        # The excluded highlight was the only one — better the same again
        # than an empty card.
        return annotation_spotlight(exclude=None, db=db, current_user=current_user)
    if not row:
        return {"highlight": None, "on_this_day": False}
    a, b = row
    return {"highlight": _to_item(a, b), "on_this_day": on_day_row is not None}


def func_now_str() -> str:
    """Today's local date as 'YYYY-MM-DD HH:MM:SS' so _month_day can slice it.

    Wrapped in a helper (not inlined) so tests can monkeypatch 'today'.
    """
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
