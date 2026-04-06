import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role, is_admin as _is_admin
from backend.models.book import Book
from backend.models.library import Library, SavedFilter, BookType
from backend.models.user import User
from backend.services.audit import audit

router = APIRouter(tags=["libraries"])


class AddBooksBody(BaseModel):
    book_ids: list[int]


# ── Schemas ───────────────────────────────────────────────────────────────────

class LibraryIn(BaseModel):
    name: str
    icon: Optional[str] = "Library"
    is_public: bool = True

class LibraryOut(BaseModel):
    id: int
    name: str
    icon: Optional[str]
    is_public: bool
    sort_order: int
    book_count: int
    assigned_user_ids: list[int] = []

    class Config:
        from_attributes = True

class SavedFilterIn(BaseModel):
    name: str
    icon: Optional[str] = "Bookmark"
    params: dict

class SavedFilterOut(BaseModel):
    id: int
    name: str
    icon: Optional[str]
    params: dict
    sort_order: int

    class Config:
        from_attributes = True

class ReorderIn(BaseModel):
    ids: list[int]  # ordered list of IDs


# ── Libraries ─────────────────────────────────────────────────────────────────

def _lib_out(lib: Library) -> LibraryOut:
    return LibraryOut(
        id=lib.id, name=lib.name, icon=lib.icon, is_public=lib.is_public,
        sort_order=lib.sort_order, book_count=len(lib.books or []),
        assigned_user_ids=[u.id for u in (lib.assigned_users or [])],
    )


# ── Libraries ─────────────────────────────────────────────────────────────────

@router.get("/libraries", response_model=list[LibraryOut])
def list_libraries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    libs = db.query(Library).filter(
        (Library.owner_id == current_user.id) | (Library.owner_id.is_(None))
    ).order_by(Library.sort_order, Library.name).all()
    return [_lib_out(l) for l in libs]


@router.post("/libraries", response_model=LibraryOut, status_code=status.HTTP_201_CREATED)
def create_library(
    body: LibraryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lib = Library(name=body.name.strip(), icon=body.icon, is_public=body.is_public, owner_id=current_user.id)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    audit(db, "libraries.created", user_id=current_user.id, username=current_user.username,
          resource_type="library", resource_id=lib.id, resource_title=lib.name)
    return _lib_out(lib)


@router.put("/libraries/{lib_id}", response_model=LibraryOut)
def update_library(
    lib_id: int,
    body: LibraryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lib = _get_library(lib_id, current_user, db)
    lib.name = body.name.strip()
    lib.icon = body.icon
    lib.is_public = body.is_public
    db.commit()
    db.refresh(lib)
    audit(db, "libraries.updated", user_id=current_user.id, username=current_user.username,
          resource_type="library", resource_id=lib.id, resource_title=lib.name)
    return _lib_out(lib)


@router.delete("/libraries/{lib_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library(
    lib_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lib = _get_library(lib_id, current_user, db)
    # Unlink any BookType that references this library so FK doesn't block delete
    db.query(BookType).filter(BookType.library_id == lib.id).update({BookType.library_id: None})
    audit(db, "libraries.deleted", user_id=current_user.id, username=current_user.username,
          resource_type="library", resource_id=lib.id, resource_title=lib.name)
    db.delete(lib)
    db.commit()


@router.post("/libraries/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_libraries(
    body: ReorderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    for i, lib_id in enumerate(body.ids):
        lib = db.query(Library).filter(Library.id == lib_id).first()
        if lib and (lib.owner_id == current_user.id or _is_admin(current_user)):
            lib.sort_order = i
    db.commit()


# ── Library book membership ───────────────────────────────────────────────────

@router.post("/libraries/{lib_id}/books", status_code=status.HTTP_204_NO_CONTENT)
def add_books_to_library(
    lib_id: int,
    body: AddBooksBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lib = _get_library(lib_id, current_user, db)
    book_ids = body.book_ids
    existing_ids = {b.id for b in lib.books}
    books = db.query(Book).filter(Book.id.in_(book_ids)).all()
    for book in books:
        if book.id not in existing_ids:
            lib.books.append(book)
    db.commit()


@router.delete("/libraries/{lib_id}/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_book_from_library(
    lib_id: int,
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lib = _get_library(lib_id, current_user, db)
    book = db.query(Book).filter(Book.id == book_id).first()
    if book and book in lib.books:
        lib.books.remove(book)
        db.commit()


# ── Library user assignment ───────────────────────────────────────────────────

class UserAssignIn(BaseModel):
    user_id: int

@router.post("/libraries/{lib_id}/users", status_code=status.HTTP_204_NO_CONTENT)
def assign_user_to_library(
    lib_id: int,
    body: UserAssignIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    lib = _get_library(lib_id, current_user, db)
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user not in lib.assigned_users:
        lib.assigned_users.append(user)
    db.commit()


@router.delete("/libraries/{lib_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_library(
    lib_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, "admin")
    lib = _get_library(lib_id, current_user, db)
    user = db.query(User).filter(User.id == user_id).first()
    if user and user in lib.assigned_users:
        lib.assigned_users.remove(user)
    db.commit()


# ── Saved filters ─────────────────────────────────────────────────────────────

@router.get("/saved-filters", response_model=list[SavedFilterOut])
def list_saved_filters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filters = db.query(SavedFilter).filter(
        (SavedFilter.owner_id == current_user.id) | (SavedFilter.owner_id.is_(None))
    ).order_by(SavedFilter.sort_order, SavedFilter.name).all()
    return [
        SavedFilterOut(id=f.id, name=f.name, icon=f.icon, params=json.loads(f.params), sort_order=f.sort_order)
        for f in filters
    ]


@router.post("/saved-filters", response_model=SavedFilterOut, status_code=status.HTTP_201_CREATED)
def create_saved_filter(
    body: SavedFilterIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sf = SavedFilter(
        name=body.name.strip(),
        icon=body.icon,
        params=json.dumps(body.params),
        owner_id=current_user.id,
    )
    db.add(sf)
    db.commit()
    db.refresh(sf)
    return SavedFilterOut(id=sf.id, name=sf.name, icon=sf.icon, params=body.params, sort_order=sf.sort_order)


@router.put("/saved-filters/{filter_id}", response_model=SavedFilterOut)
def update_saved_filter(
    filter_id: int,
    body: SavedFilterIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sf = db.query(SavedFilter).filter(
        SavedFilter.id == filter_id,
        SavedFilter.owner_id == current_user.id,
    ).first()
    if not sf:
        raise HTTPException(status_code=404, detail="Saved filter not found")
    sf.name = body.name.strip()
    sf.icon = body.icon
    sf.params = json.dumps(body.params)
    db.commit()
    db.refresh(sf)
    return SavedFilterOut(id=sf.id, name=sf.name, icon=sf.icon, params=body.params, sort_order=sf.sort_order)


@router.delete("/saved-filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_filter(
    filter_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sf = db.query(SavedFilter).filter(
        SavedFilter.id == filter_id,
        SavedFilter.owner_id == current_user.id,
    ).first()
    if not sf:
        raise HTTPException(status_code=404, detail="Saved filter not found")
    db.delete(sf)
    db.commit()


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_library(lib_id: int, user: User, db: Session) -> Library:
    lib = db.query(Library).filter(Library.id == lib_id).first()
    if not lib:
        raise HTTPException(status_code=404, detail="Library not found")
    if lib.owner_id is not None and lib.owner_id != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Not your library")
    return lib
