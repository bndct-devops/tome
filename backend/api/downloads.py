import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User

router = APIRouter()


class DownloadRequest(BaseModel):
    book_ids: list[int]


@router.post("/downloads")
def bulk_download(
    body: DownloadRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not body.book_ids:
        raise HTTPException(400, "No books selected")
    if len(body.book_ids) > 200:
        raise HTTPException(400, "Too many books (max 200)")

    books = db.query(Book).filter(Book.id.in_(body.book_ids)).all()
    if not books:
        raise HTTPException(404, "No books found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for book in books:
            author = (book.author or "Unknown Author").replace("/", "-")[:60]
            title = book.title.replace("/", "-")[:80]
            folder = f"{author} - {title}"
            for f in book.files:
                path = Path(f.file_path)
                if path.exists():
                    zf.write(str(path), f"{folder}/{path.name}")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="tome-books.zip"'},
    )
