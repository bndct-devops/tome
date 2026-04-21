from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_arc_indices(start: float, end: float) -> None:
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="start_index must be <= end_index",
        )
