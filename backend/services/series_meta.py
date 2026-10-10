"""Shared series status and arc code paths.

``PUT /series/{name}/meta`` and ``POST /series/{name}/arcs/bulk`` call these,
and so does the AI series cleanup apply. Nothing here commits: callers own the
transaction and the audit entry.
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence

from sqlalchemy.orm import Session

from backend.models.series_meta import Arc, SeriesMeta

VALID_STATUSES = {"ongoing", "finished", "hiatus", "unknown"}


class ArcError(ValueError):
    """An arc list the API should answer with 400."""


class ArcInput(Protocol):
    name: str
    start_index: float
    end_index: float
    description: Optional[str]


def validate_arc_indices(start: float, end: float) -> None:
    if start > end:
        raise ArcError("start_index must be <= end_index")


def check_no_overlap(arcs: Sequence[ArcInput]) -> None:
    """Raise ArcError when two arcs share a volume range (inclusive bounds)."""
    ordered = sorted(arcs, key=lambda a: (a.start_index, a.end_index))
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start_index <= prev.end_index:
            raise ArcError(f"Arcs '{prev.name}' and '{cur.name}' overlap")


def series_status(db: Session, name: str) -> str:
    meta = db.query(SeriesMeta).filter(SeriesMeta.series_name == name).first()
    return meta.status if meta else "unknown"


def set_series_status(db: Session, name: str, status: str) -> SeriesMeta:
    """Upsert the SeriesMeta status for a series. Does not commit."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
    meta = db.query(SeriesMeta).filter(SeriesMeta.series_name == name).first()
    if meta is None:
        meta = SeriesMeta(series_name=name, status=status)
        db.add(meta)
    else:
        meta.status = status
    db.flush()
    return meta


def list_arcs(db: Session, name: str) -> list[Arc]:
    return db.query(Arc).filter(Arc.series_name == name).order_by(Arc.start_index).all()


def sync_arcs(db: Session, name: str, arcs: Sequence[ArcInput]) -> None:
    """Diff-sync the arcs of a series by name: update matching names, create
    new ones, delete the ones absent from ``arcs``. Validates each arc's
    indices (ArcError). Does not commit."""
    for arc_in in arcs:
        validate_arc_indices(arc_in.start_index, arc_in.end_index)

    existing: dict[str, Arc] = {arc.name: arc for arc in db.query(Arc).filter(Arc.series_name == name).all()}
    incoming_names = {arc_in.name for arc_in in arcs}

    for arc_name, arc in list(existing.items()):
        if arc_name not in incoming_names:
            db.delete(arc)
    # Flush deletes first so a re-created name never trips the unique constraint.
    db.flush()

    for arc_in in arcs:
        if arc_in.name in existing:
            arc = existing[arc_in.name]
            arc.start_index = arc_in.start_index
            arc.end_index = arc_in.end_index
            arc.description = arc_in.description
        else:
            db.add(Arc(
                series_name=name,
                name=arc_in.name,
                start_index=arc_in.start_index,
                end_index=arc_in.end_index,
                description=arc_in.description,
            ))
    db.flush()


def move_series_meta(db: Session, old: str, new: str) -> None:
    """Carry a series' status and arcs over to its new name on a rename.
    Leaves them where they are when the new name already has its own (a merge
    into an existing series keeps that series' data). Does not commit."""
    if old == new:
        return
    old_meta = db.query(SeriesMeta).filter(SeriesMeta.series_name == old).first()
    if old_meta is not None and db.query(SeriesMeta).filter(SeriesMeta.series_name == new).first() is None:
        old_meta.series_name = new
    old_arcs = db.query(Arc).filter(Arc.series_name == old).all()
    if old_arcs and db.query(Arc.id).filter(Arc.series_name == new).first() is None:
        for arc in old_arcs:
            arc.series_name = new
    db.flush()
