import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.permissions import book_visibility_filter, is_admin as _is_admin
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User
from backend.services.audit import audit

router = APIRouter()


class DownloadRequest(BaseModel):
    book_ids: list[int]


@router.post("/downloads")
def bulk_download(
    body: DownloadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not body.book_ids:
        raise HTTPException(400, "No books selected")
    if len(body.book_ids) > 200:
        raise HTTPException(400, "Too many books (max 200)")

    q = db.query(Book).filter(Book.id.in_(body.book_ids))
    if not _is_admin(current_user):
        q = q.filter(book_visibility_filter(db, current_user))
    books = q.all()
    if not books:
        raise HTTPException(404, "No books found")

    from backend.services.metadata_embed import get_baked_path
    from backend.services.download_quota import enforce_download_limit, record_download
    from backend.services.ko_hash import record_served_artifact

    # The quota counts files, so enforce against the number of files this zip
    # will actually contain — before any expensive baking/zipping starts.
    servable = [
        (book, f)
        for book in books
        for f in book.files
        if Path(f.file_path).exists()
    ]
    enforce_download_limit(db, current_user, count=len(servable))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for book, f in servable:
            author = (book.author or "Unknown Author").replace("/", "-")[:60]
            title = book.title.replace("/", "-")[:80]
            folder = f"{author} - {title}"
            # Serve a copy with Tome's metadata baked in, like every other
            # download path (single/OPDS/TomeSync). Falls back to the raw
            # file if baking fails. Keep the original filename in the zip.
            serve = get_baked_path(book, f)
            record_served_artifact(db, book.id, f, serve)
            zf.write(str(serve), f"{folder}/{Path(f.file_path).name}")
            record_download(db, current_user, book.id)

    buf.seek(0)
    # Parity with the single-download path, which audits each download.
    audit(db, "books.bulk_downloaded", user_id=current_user.id, username=current_user.username,
          details={"book_count": len(books), "requested": len(body.book_ids)})
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="tome-books.zip"'},
    )
