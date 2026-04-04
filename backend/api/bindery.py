"""
Bindery API — manage files in the incoming/ drop zone.

Endpoints:
  GET  /api/bindery/count    — lightweight badge count
  GET  /api/bindery          — list pending files with parsed metadata
  POST /api/bindery/preview  — fetch metadata candidates for a single file
  POST /api/bindery/accept   — accept files into the library
  POST /api/bindery/reject   — delete files from the bindery
"""
import logging
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book, BookFile, BookTag
from backend.models.user import User
from backend.services.audit import audit
from backend.services.book_types import assign_book_to_type_library
from backend.services.filename_parser import parse_filename
from backend.services.metadata import extract_metadata, sha256_file
from backend.services.metadata_fetch import fetch_candidates
from backend.services.organizer import get_library_path, resolve_unique_path

router = APIRouter(tags=["bindery"])
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}


# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------

def _require_bindery(current_user: User) -> None:
    if current_user.is_admin:
        return
    if current_user.permissions and current_user.permissions.can_approve_bindery:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bindery permission required",
    )


# ---------------------------------------------------------------------------
# Path safety helper
# ---------------------------------------------------------------------------

def _safe_resolve(rel_path: str) -> Path:
    """Resolve a relative path inside incoming_dir. Raises 400 on traversal."""
    incoming = settings.incoming_dir.resolve()
    resolved = (incoming / rel_path).resolve()
    if not str(resolved).startswith(str(incoming)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path",
        )
    return resolved


# ---------------------------------------------------------------------------
# Empty-dir cleanup helper
# ---------------------------------------------------------------------------

def _cleanup_empty_dirs(start: Path) -> None:
    """Walk upward from start, removing empty directories.

    Never removes incoming_dir itself or incoming_dir/chapters/.
    """
    incoming = settings.incoming_dir.resolve()
    protected = {incoming, incoming / "chapters"}

    current = start.resolve()
    while current != incoming and current != current.parent:
        if current in protected:
            break
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
            else:
                break
        except OSError:
            break
        current = current.parent


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class BinderyItem(BaseModel):
    path: str
    filename: str
    size: int
    modified: float
    format: str
    content_type: str
    series: str | None
    series_index: float | None
    title: str
    folder: str | None


class PreviewRequest(BaseModel):
    path: str
    query: str | None = None  # manual search override


class BinderyAcceptFile(BaseModel):
    path: str
    title: str
    author: str | None = None
    series: str | None = None
    series_index: float | None = None
    content_type: str = "volume"
    book_type_id: int | None = None
    description: str | None = None
    publisher: str | None = None
    year: int | None = None
    isbn: str | None = None
    language: str | None = None
    cover_url: str | None = None
    tags: list[str] = []


class BinderyAcceptRequest(BaseModel):
    files: list[BinderyAcceptFile]


class RejectRequest(BaseModel):
    paths: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/count")
def bindery_count(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the number of supported files waiting in the bindery."""
    _require_bindery(current_user)

    incoming = settings.incoming_dir
    count = 0
    for p in incoming.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                count += 1
    return {"count": count}


@router.get("", response_model=list[BinderyItem])
def bindery_list(
    current_user: User = Depends(get_current_user),
) -> list[BinderyItem]:
    """List all pending files in the bindery with parsed metadata."""
    _require_bindery(current_user)

    incoming = settings.incoming_dir.resolve()
    items: list[BinderyItem] = []

    for p in incoming.rglob("*"):
        if not p.is_file():
            continue
        # Skip hidden files and files inside hidden directories
        rel = p.relative_to(incoming)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        rel_str = str(rel)
        rel_parts = rel.parts

        # in_chapters_dir: file path starts with "chapters/"
        in_chapters_dir = len(rel_parts) > 1 and rel_parts[0].lower() == "chapters"

        parsed = parse_filename(p.name, in_chapters_dir=in_chapters_dir)

        stat = p.stat()

        # folder: the immediate parent dir name if not incoming_dir root
        folder: str | None = None
        if len(rel_parts) > 1:
            folder = rel_parts[-2]  # immediate parent directory name

        items.append(
            BinderyItem(
                path=rel_str,
                filename=p.name,
                size=stat.st_size,
                modified=stat.st_mtime,
                format=p.suffix.lower().lstrip("."),
                content_type=parsed.content_type,
                series=parsed.series,
                series_index=parsed.series_index,
                title=parsed.title,
                folder=folder,
            )
        )

    # Sort: folder (grouped, None last), then filename
    items.sort(key=lambda i: (i.folder or "\xff", i.filename))
    return items


@router.post("/preview")
async def bindery_preview(
    body: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Fetch metadata candidates for a single bindery file without creating a Book."""
    _require_bindery(current_user)

    full_path = _safe_resolve(body.path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    meta = extract_metadata(full_path, settings.covers_dir)

    fetch_result = await fetch_candidates(
        title=meta.get("title") or full_path.stem,
        author=meta.get("author"),
        isbn=meta.get("isbn"),
        series=meta.get("series"),
        series_index=meta.get("series_index"),
        query_override=body.query,
    )

    # Serialise MetadataCandidate dataclasses to dicts
    candidates = [
        {
            "source": c.source,
            "source_id": c.source_id,
            "title": c.title,
            "author": c.author,
            "description": c.description,
            "cover_url": c.cover_url,
            "publisher": c.publisher,
            "year": c.year,
            "page_count": c.page_count,
            "isbn": c.isbn,
            "language": c.language,
            "tags": c.tags,
            "series": c.series,
            "series_index": c.series_index,
        }
        for c in fetch_result.candidates
    ]

    # Strip internal private keys from file_metadata before returning
    file_metadata = {k: v for k, v in meta.items() if not k.startswith("_")}

    return {"file_metadata": file_metadata, "candidates": candidates, "query_used": fetch_result.query_used}


@router.post("/accept")
def bindery_accept(
    body: BinderyAcceptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Accept files from the bindery into the library."""
    _require_bindery(current_user)

    accepted: list[dict] = []
    errors: list[dict] = []

    for item in body.files:
        try:
            full_path = _safe_resolve(item.path)
            if not full_path.exists():
                errors.append({"path": item.path, "error": "File not found"})
                continue

            suffix = full_path.suffix.lower().lstrip(".")
            content_hash = sha256_file(full_path)
            file_size = full_path.stat().st_size

            # Extract metadata for cover even if user has provided all other fields
            meta = extract_metadata(full_path, settings.covers_dir)

            # Build meta dict for library path calculation from provided fields
            path_meta: dict = {
                "title": item.title,
                "author": item.author,
                "series": item.series,
                "series_index": item.series_index,
                "year": item.year,
            }

            # Determine destination path
            rel = get_library_path(path_meta, full_path.name)
            dest = resolve_unique_path(settings.library_dir, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Move the file
            shutil.move(str(full_path), str(dest))

            # Determine cover: use extracted cover or download from cover_url
            cover_path: str | None = meta.get("cover_path")
            if item.cover_url:
                try:
                    with httpx.Client(follow_redirects=True, timeout=15) as client:
                        resp = client.get(item.cover_url)
                        resp.raise_for_status()
                    from backend.services.metadata import save_cover
                    cover_path = save_cover(resp.content, settings.covers_dir, content_hash)
                except Exception as exc:
                    logger.warning("Failed to download cover from %s: %s", item.cover_url, exc)

            # Create Book record
            book = Book(
                title=item.title,
                author=item.author,
                series=item.series,
                series_index=item.series_index,
                isbn=item.isbn,
                publisher=item.publisher,
                description=item.description,
                language=item.language,
                year=item.year,
                cover_path=cover_path,
                content_hash=content_hash,
                content_type=item.content_type,
                status="active",
                added_by=current_user.id,
                book_type_id=item.book_type_id,
            )
            db.add(book)
            db.flush()

            # Create BookFile record
            db.add(BookFile(
                book_id=book.id,
                file_path=str(dest.resolve()),
                format=suffix,
                file_size=dest.stat().st_size,
                content_hash=content_hash,
            ))

            # Create tag records
            for tag_str in item.tags:
                tag_str = tag_str.strip()
                if tag_str:
                    db.add(BookTag(book_id=book.id, tag=tag_str, source="bindery"))

            db.commit()

            # Assign to book type library
            if item.book_type_id:
                from backend.models.library import BookType
                bt = db.get(BookType, item.book_type_id)
                if bt:
                    assign_book_to_type_library(db, book, bt)

            db.refresh(book)

            # Audit
            audit(
                db,
                "bindery.accepted",
                user_id=current_user.id,
                username=current_user.username,
                resource_type="book",
                resource_id=book.id,
                resource_title=book.title,
                details={"format": suffix, "source_path": item.path},
            )

            # Clean up empty directories left behind in bindery
            _cleanup_empty_dirs(full_path.parent)

            accepted.append({"book_id": book.id, "title": book.title})

        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to accept bindery file %s", item.path)
            db.rollback()
            errors.append({"path": item.path, "error": str(exc)})

    return {"accepted": accepted, "errors": errors}


@router.post("/reject")
def bindery_reject(
    body: RejectRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete files from the bindery without importing them."""
    _require_bindery(current_user)

    rejected = 0
    errors: list[dict] = []

    for rel_path in body.paths:
        try:
            full_path = _safe_resolve(rel_path)
            if not full_path.exists():
                errors.append({"path": rel_path, "error": "File not found"})
                continue

            parent = full_path.parent
            full_path.unlink()
            rejected += 1

            # Clean up empty parent directories
            _cleanup_empty_dirs(parent)

        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to reject bindery file %s", rel_path)
            errors.append({"path": rel_path, "error": str(exc)})

    return {"rejected": rejected, "errors": errors}
