from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role, is_admin
from backend.models.series_meta import Arc, SeriesMeta
from backend.models.user import User
from backend.schemas.series import (
    ArcCreate,
    ArcOut,
    ArcUpdate,
    SeriesMetaOut,
    SeriesMetaUpdate,
)

router = APIRouter(tags=["series"])

VALID_STATUSES = {"ongoing", "finished", "hiatus", "unknown"}


# ── Series reading-stats endpoint ─────────────────────────────────────────────
# Registered before /series/{name}/arcs and /series/{name}/meta so the
# static suffix "reading-stats" is matched first.

@router.get("/series/{name}/reading-stats")
def get_series_reading_stats(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the current user's reading statistics across all visible books in a series.

    Admins additionally receive a library-wide aggregate (all users).
    The ``name`` path parameter arrives URL-decoded by FastAPI.
    """
    from backend.services.reading_stats import (
        compute_series_reading_stats,
        compute_series_aggregate_stats,
    )

    own = compute_series_reading_stats(db, user=current_user, series_name=name)
    aggregate = (
        compute_series_aggregate_stats(db, series_name=name)
        if is_admin(current_user)
        else None
    )

    return {"own": own, "aggregate": aggregate}


# ── Arc endpoints ─────────────────────────────────────────────────────────────

@router.get("/series/{name}/arcs", response_model=list[ArcOut])
def list_arcs(
    name: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Return all arcs for a series, sorted by start_index."""
    arcs = (
        db.query(Arc)
        .filter(Arc.series_name == name)
        .order_by(Arc.start_index)
        .all()
    )
    return arcs


@router.post("/arcs", response_model=ArcOut, status_code=status.HTTP_201_CREATED)
def create_arc(
    body: ArcCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new arc. Admin only."""
    require_role(current_user, "admin")
    _validate_arc_indices(body.start_index, body.end_index)

    arc = Arc(
        series_name=body.series_name,
        name=body.name,
        start_index=body.start_index,
        end_index=body.end_index,
        description=body.description,
    )
    db.add(arc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="An arc with this name already exists for that series",
        )
    db.refresh(arc)
    return arc


@router.patch("/arcs/{arc_id}", response_model=ArcOut)
def update_arc(
    arc_id: int,
    body: ArcUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Partially update an arc. Admin only."""
    require_role(current_user, "admin")
    arc = db.get(Arc, arc_id)
    if not arc:
        raise HTTPException(status_code=404, detail="Arc not found")

    if body.name is not None:
        arc.name = body.name
    if body.description is not None:
        arc.description = body.description

    new_start = body.start_index if body.start_index is not None else arc.start_index
    new_end = body.end_index if body.end_index is not None else arc.end_index
    _validate_arc_indices(new_start, new_end)

    arc.start_index = new_start
    arc.end_index = new_end

    db.commit()
    db.refresh(arc)
    return arc


@router.delete("/arcs/{arc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_arc(
    arc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an arc. Admin only."""
    require_role(current_user, "admin")
    arc = db.get(Arc, arc_id)
    if not arc:
        raise HTTPException(status_code=404, detail="Arc not found")
    db.delete(arc)
    db.commit()


# ── Bulk arc endpoint — must be registered before any /{arc_id} catch-alls ───

@router.post("/series/{name}/arcs/bulk", response_model=list[ArcOut])
def bulk_upsert_arcs(
    name: str,
    body: list[ArcCreate],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Diff-sync arcs for a series in one transaction.

    Given the incoming list:
      - Arcs whose ``name`` matches an existing arc → update if changed.
      - Arcs whose ``name`` does not exist → create.
      - Existing arcs whose ``name`` is absent from the payload → delete.

    Returns the canonical list sorted by start_index.
    """
    require_role(current_user, "admin")

    for arc_in in body:
        _validate_arc_indices(arc_in.start_index, arc_in.end_index)

    existing: dict[str, Arc] = {
        arc.name: arc
        for arc in db.query(Arc).filter(Arc.series_name == name).all()
    }

    incoming_names = {arc_in.name for arc_in in body}

    # Delete arcs not in the incoming payload
    for arc_name, arc in list(existing.items()):
        if arc_name not in incoming_names:
            db.delete(arc)

    # Create or update
    for arc_in in body:
        if arc_in.name in existing:
            arc = existing[arc_in.name]
            arc.start_index = arc_in.start_index
            arc.end_index = arc_in.end_index
            arc.description = arc_in.description
        else:
            arc = Arc(
                series_name=name,
                name=arc_in.name,
                start_index=arc_in.start_index,
                end_index=arc_in.end_index,
                description=arc_in.description,
            )
            db.add(arc)

    db.commit()

    return (
        db.query(Arc)
        .filter(Arc.series_name == name)
        .order_by(Arc.start_index)
        .all()
    )


# ── SeriesMeta endpoints ──────────────────────────────────────────────────────

@router.get("/series/meta-map", response_model=dict[str, str])
def list_series_meta_map(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Return a {series_name: status} dict for every SeriesMeta row.

    Cheap one-shot lookup for dashboards that render a status badge per
    series — avoids N parallel GET /series/{name}/meta calls that can
    exhaust the DB connection pool.
    """
    rows = db.query(SeriesMeta.series_name, SeriesMeta.status).all()
    return {r.series_name: r.status for r in rows}


@router.get("/series/{name}/meta", response_model=SeriesMetaOut)
def get_series_meta(
    name: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Return SeriesMeta for a series. Returns a placeholder with status='unknown' if none exists."""
    meta = db.query(SeriesMeta).filter(SeriesMeta.series_name == name).first()
    if meta is None:
        return SeriesMetaOut(series_name=name, status="unknown")
    return meta


@router.put("/series/{name}/meta", response_model=SeriesMetaOut)
def upsert_series_meta(
    name: str,
    body: SeriesMetaUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert the SeriesMeta for a series. Admin only."""
    require_role(current_user, "admin")
    meta = db.query(SeriesMeta).filter(SeriesMeta.series_name == name).first()
    if meta is None:
        meta = SeriesMeta(series_name=name, status=body.status)
        db.add(meta)
    else:
        meta.status = body.status
    db.commit()
    db.refresh(meta)
    return meta


# ── Series rating (per-user) ──────────────────────────────────────────────────
# A volume's effective rating is its own rating else this series rating
# (inherited). Series *display* rating is the explicit value if set, else the
# average of the user's volume ratings. The "__unserialized__" sentinel + empty
# string are the No-Series bucket and can never be rated.

from pydantic import BaseModel
from sqlalchemy import func as _func
from backend.models.user_series_rating import UserSeriesRating
from backend.models.user_book_status import UserBookStatus
from backend.models.book import Book

NO_SERIES_SENTINEL = "__unserialized__"


class SeriesRatingOut(BaseModel):
    series_name: str
    rating: Optional[int] = None        # explicit series rating
    review: Optional[str] = None
    volume_average: Optional[float] = None  # avg of the user's volume ratings
    rated_volumes: int = 0
    display: Optional[int] = None        # explicit if set, else rounded average


class SeriesRatingIn(BaseModel):
    rating: Optional[int] = None         # 1–5, or null to clear
    review: Optional[str] = None


def _series_rating_out(db: Session, user: User, name: str) -> "SeriesRatingOut":
    row = (
        db.query(UserSeriesRating)
        .filter(UserSeriesRating.user_id == user.id, UserSeriesRating.series_name == name)
        .first()
    )
    avg, cnt = (
        db.query(_func.avg(UserBookStatus.rating), _func.count(UserBookStatus.rating))
        .join(Book, Book.id == UserBookStatus.book_id)
        .filter(
            UserBookStatus.user_id == user.id,
            UserBookStatus.rating.isnot(None),
            Book.series == name,
        )
        .one()
    )
    explicit = row.rating if row else None
    display = explicit if explicit is not None else (round(avg) if avg is not None else None)
    return SeriesRatingOut(
        series_name=name,
        rating=explicit,
        review=row.review if row else None,
        volume_average=round(float(avg), 2) if avg is not None else None,
        rated_volumes=int(cnt or 0),
        display=display,
    )


def _reject_no_series(name: str) -> None:
    if not name or name == NO_SERIES_SENTINEL:
        raise HTTPException(400, "The 'No Series' group cannot be rated")


@router.get("/series/{name}/rating", response_model=SeriesRatingOut)
def get_series_rating(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _series_rating_out(db, current_user, name)


@router.put("/series/{name}/rating", response_model=SeriesRatingOut)
def set_series_rating(
    name: str,
    body: SeriesRatingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _reject_no_series(name)
    if body.rating is not None and not (1 <= body.rating <= 5):
        raise HTTPException(400, "rating must be between 1 and 5, or null to clear")
    from datetime import datetime
    raw = body.model_dump(exclude_unset=True)
    row = (
        db.query(UserSeriesRating)
        .filter(UserSeriesRating.user_id == current_user.id, UserSeriesRating.series_name == name)
        .first()
    )
    if not row:
        row = UserSeriesRating(user_id=current_user.id, series_name=name)
        db.add(row)
    if "rating" in raw:
        row.rating = body.rating
        row.rated_at = datetime.utcnow() if body.rating is not None else None
    if "review" in raw:
        row.review = body.review or None
    db.commit()
    return _series_rating_out(db, current_user, name)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_arc_indices(start: float, end: float) -> None:
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="start_index must be <= end_index",
        )
