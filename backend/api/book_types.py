from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role
from backend.models.library import BookType
from backend.models.user import User
from backend.services.audit import audit
from backend.services.book_types import normalize_folder
from backend.services.layout import (
    DEFAULT_PRESET,
    PRESETS,
    normalize_preset,
    render_layout,
    validate_template,
)

router = APIRouter(tags=["book-types"])


class BookTypeIn(BaseModel):
    slug: Optional[str] = None
    label: str
    icon: str = "BookOpen"
    color: str = "blue"
    sort_order: int = 0
    # Folder inside the library for books of this type (#270). None on an
    # update leaves the stored folder alone; "" clears it.
    folder: Optional[str] = None
    # Layout inside the folder (#276). None on an update keeps the stored
    # value; layout_template "" turns Advanced off.
    layout_preset: Optional[str] = None
    layout_template: Optional[str] = None


def _folder_or_400(raw: Optional[str]) -> str:
    try:
        return normalize_folder(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _preset_or_400(raw: Optional[str]) -> str:
    try:
        return normalize_preset(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _template_or_400(raw: Optional[str]) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    err = validate_template(s)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return s


class BookTypeOut(BaseModel):
    id: int
    slug: str
    label: str
    icon: str
    color: str
    sort_order: int
    folder: str = ""
    layout_preset: str = DEFAULT_PRESET
    layout_template: str = ""
    library_id: Optional[int] = None

    class Config:
        from_attributes = True


@router.get("/book-types", response_model=list[BookTypeOut])
def list_book_types(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(BookType).order_by(BookType.sort_order, BookType.label).all()


@router.post("/book-types", response_model=BookTypeOut, status_code=status.HTTP_201_CREATED)
def create_book_type(
    body: BookTypeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    raw_slug = body.slug or body.label
    slug = raw_slug.strip().lower().replace(" ", "_")
    if db.query(BookType).filter(BookType.slug == slug).first():
        raise HTTPException(status_code=400, detail="Slug already exists")
    folder = _folder_or_400(body.folder)
    preset = _preset_or_400(body.layout_preset)
    template = _template_or_400(body.layout_template)
    bt = BookType(slug=slug, label=body.label.strip(), icon=body.icon, color=body.color,
                  sort_order=body.sort_order, folder=folder,
                  layout_preset=preset, layout_template=template)
    db.add(bt)
    db.commit()
    db.refresh(bt)
    audit(db, "book_type.created", user_id=current_user.id, username=current_user.username,
          resource_type="book_type", resource_id=bt.id, resource_title=bt.label)
    return bt


@router.put("/book-types/{bt_id}", response_model=BookTypeOut)
def update_book_type(
    bt_id: int,
    body: BookTypeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    bt = db.get(BookType, bt_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Not found")
    # Validate everything before changing anything.
    folder = _folder_or_400(body.folder) if body.folder is not None else None
    preset = _preset_or_400(body.layout_preset) if body.layout_preset is not None else None
    template = _template_or_400(body.layout_template) if body.layout_template is not None else None
    bt.label = body.label.strip()
    bt.icon = body.icon
    bt.color = body.color
    bt.sort_order = body.sort_order
    # Changing the folder or the layout never moves files: existing books of
    # this type show up as misplaced in Library Health until Reorganize runs.
    if folder is not None:
        bt.folder = folder
    if preset is not None:
        bt.layout_preset = preset
    if template is not None:
        bt.layout_template = template
    db.commit()
    db.refresh(bt)
    audit(db, "book_type.updated", user_id=current_user.id, username=current_user.username,
          resource_type="book_type", resource_id=bt.id, resource_title=bt.label)
    return bt


# ── Layout preview (#276) ─────────────────────────────────────────────────────

PREVIEW_LIMIT = 6

# Shown when the library has no books yet: one with a series, one without,
# one without a year.
SAMPLE_BOOKS = [
    {"title": "Deal Breaker", "author": "Harlan Coben", "series": "Myron Bolitar",
     "series_index": 1.0, "year": 1995, "filename": "deal-breaker.epub"},
    {"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin", "series": None,
     "series_index": None, "year": 1969, "filename": "left-hand.epub"},
    {"title": "Meditations", "author": "Marcus Aurelius", "series": None,
     "series_index": None, "year": None, "filename": "meditations.epub"},
]


class LayoutPreviewIn(BaseModel):
    preset: Optional[str] = None
    template: Optional[str] = None
    book_type_id: Optional[int] = None
    # The folder as typed in the form; None uses the stored one of the type.
    folder: Optional[str] = None


def _pick_preview_books(books: list) -> list:
    """Up to PREVIEW_LIMIT books, starting with a mix: one with a series, one
    without, one without a year; then the rest in order, one per series."""
    picked: list = []

    def take(pred):
        for b in books:
            if b not in picked and pred(b):
                picked.append(b)
                return

    take(lambda b: b.series)
    take(lambda b: not b.series)
    take(lambda b: not b.year)
    seen_series = {b.series for b in picked if b.series}
    for b in books:
        if len(picked) >= PREVIEW_LIMIT:
            break
        if b in picked or (b.series and b.series in seen_series):
            continue
        picked.append(b)
        if b.series:
            seen_series.add(b.series)
    for b in books:
        if len(picked) >= PREVIEW_LIMIT:
            break
        if b not in picked:
            picked.append(b)
    return picked[:PREVIEW_LIMIT]


def _preview_samples(db: Session, book_type_id: Optional[int]) -> tuple[list[dict], str]:
    """(metas, source): the type's books, else any books, else SAMPLE_BOOKS."""
    from sqlalchemy.orm import joinedload
    from backend.models.book import Book

    def load(q):
        return (q.options(joinedload(Book.files)).filter(Book.status == "active", Book.files.any())
                .order_by(Book.series, Book.series_index, Book.title).limit(200).all())

    source = "sample"
    books: list = []
    if book_type_id:
        books = load(db.query(Book).filter(Book.book_type_id == book_type_id))
        source = "type"
    if not books:
        books = load(db.query(Book))
        source = "library"
    if not books:
        return [dict(s) for s in SAMPLE_BOOKS], "sample"
    from pathlib import Path as _P
    out = []
    for b in _pick_preview_books(books):
        out.append({
            "title": b.title, "author": b.author, "series": b.series,
            "series_index": b.series_index, "year": b.year,
            "filename": _P(b.files[0].file_path).name,
        })
    return out, source


def _render_rows(samples: list[dict], folder: str, preset: str, template: str) -> list[dict]:
    from backend.services.organizer import get_library_path
    from backend.services.layout import TypeLayout
    layout = TypeLayout(folder=folder, preset=preset, template=template)
    return [{"title": s["title"] or "", "path": str(get_library_path(s, s["filename"], layout))}
            for s in samples]


@router.post("/book-types/layout-preview")
def layout_preview(
    body: LayoutPreviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Where real books of a type would go under a preset or template (#276).

    Returns ``paths`` for the requested preset/template (empty with ``error``
    when the template is invalid) and ``presets`` with the same books under
    every preset, for the examples next to each choice. Moves nothing.
    """
    require_role(current_user, "admin")
    preset = _preset_or_400(body.preset)
    if body.folder is not None:
        folder = _folder_or_400(body.folder)
    elif body.book_type_id:
        bt = db.get(BookType, body.book_type_id)
        folder = (bt.folder or "") if bt else ""
    else:
        folder = ""
    samples, source = _preview_samples(db, body.book_type_id)
    template = (body.template or "").strip()
    error = validate_template(template) if template else None
    paths = [] if error else _render_rows(samples, folder, preset, template)
    return {
        "paths": paths,
        "error": error,
        "source": source,
        "presets": {
            name: {
                "label": info["label"],
                "description": info["description"],
                "template": info["template"],
                "paths": _render_rows(samples, folder, name, ""),
            }
            for name, info in PRESETS.items()
        },
    }


@router.delete("/book-types/{bt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book_type(
    bt_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    bt = db.get(BookType, bt_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Not found")
    from backend.models.book import Book
    if db.query(Book).filter(Book.book_type_id == bt_id).count() > 0:
        raise HTTPException(status_code=400, detail="Cannot delete: books still use this type")
    label = bt.label
    db.delete(bt)
    db.commit()
    audit(db, "book_type.deleted", user_id=current_user.id, username=current_user.username,
          resource_type="book_type", resource_id=bt_id, resource_title=label)
