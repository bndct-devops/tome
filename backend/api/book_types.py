from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.core.permissions import require_role
from backend.models.library import BookType
from backend.models.user import User

router = APIRouter(tags=["book-types"])


class BookTypeIn(BaseModel):
    slug: str
    label: str
    icon: str = "BookOpen"
    color: str = "blue"
    sort_order: int = 0

class BookTypeOut(BaseModel):
    id: int
    slug: str
    label: str
    icon: str
    color: str
    sort_order: int
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
    slug = body.slug.strip().lower().replace(" ", "_")
    if db.query(BookType).filter(BookType.slug == slug).first():
        raise HTTPException(status_code=400, detail="Slug already exists")
    bt = BookType(slug=slug, label=body.label.strip(), icon=body.icon, color=body.color, sort_order=body.sort_order)
    db.add(bt)
    db.commit()
    db.refresh(bt)
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
    bt.label = body.label.strip()
    bt.icon = body.icon
    bt.color = body.color
    bt.sort_order = body.sort_order
    db.commit()
    db.refresh(bt)
    return bt


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
    db.delete(bt)
    db.commit()
