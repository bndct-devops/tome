"""Admin duplicate detection endpoints.

Provides four detection strategies:
1. Same content_hash (exact file duplicates)
2. Same ISBN
3. Same author + series + series_index (different editions of same volume)
4. Similar title+author (SequenceMatcher ratio > 0.85, skips different volumes)

Dismissed pairs are persisted in the duplicate_dismissals table and excluded
from subsequent GET results.
"""
import difflib
import itertools
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book, BookTag
from backend.models.duplicate_dismissal import DuplicateDismissal
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.audit import audit

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class BookFileOut(BaseModel):
    id: int
    format: str
    file_size: Optional[int]

    model_config = {"from_attributes": True}


class DuplicateBookOut(BaseModel):
    id: int
    title: str
    subtitle: Optional[str]
    author: Optional[str]
    isbn: Optional[str]
    cover_path: Optional[str]
    series: Optional[str]
    series_index: Optional[float]
    year: Optional[int]
    files: list[BookFileOut]
    tags: list[str]
    library_ids: list[int]

    model_config = {"from_attributes": True}


class DuplicateGroup(BaseModel):
    group_id: str
    match_reason: str  # "content_hash" | "isbn" | "similar_title"
    books: list[DuplicateBookOut]


class DuplicatesResponse(BaseModel):
    groups: list[DuplicateGroup]


class MergeBody(BaseModel):
    keep_id: int
    remove_ids: list[int]


class DismissBody(BaseModel):
    book_ids: list[int]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _book_to_out(book: Book) -> DuplicateBookOut:
    return DuplicateBookOut(
        id=book.id,
        title=book.title,
        subtitle=book.subtitle,
        author=book.author,
        isbn=book.isbn,
        cover_path=book.cover_path,
        series=book.series,
        series_index=book.series_index,
        year=book.year,
        files=[BookFileOut(id=f.id, format=f.format, file_size=f.file_size) for f in book.files],
        tags=[t.tag for t in book.tags],
        library_ids=book.library_ids,
    )


def _dismissed_set(db: Session) -> set[frozenset[int]]:
    """Load all dismissed pairs as a set of frozensets for O(1) lookup."""
    rows = db.query(DuplicateDismissal).all()
    return {frozenset([r.book_id_a, r.book_id_b]) for r in rows}


def _deduplicate_groups(
    groups: list[list[Book]],
    dismissed: set[frozenset[int]],
) -> list[list[Book]]:
    """Remove groups where every pair of books has been dismissed."""
    result = []
    for group in groups:
        ids = [b.id for b in group]
        all_dismissed = all(
            frozenset([a, b]) in dismissed
            for a, b in itertools.combinations(ids, 2)
        )
        if not all_dismissed:
            result.append(group)
    return result


# ── GET /admin/duplicates ─────────────────────────────────────────────────────

@router.get("/admin/duplicates", response_model=DuplicatesResponse)
def get_duplicates(
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    dismissed = _dismissed_set(db)

    # Strategy 1: same content_hash
    hash_dupes: list[list[Book]] = []
    hash_counts = (
        db.query(Book.content_hash, func.count(Book.id).label("cnt"))
        .filter(Book.content_hash.isnot(None))
        .group_by(Book.content_hash)
        .having(func.count(Book.id) > 1)
        .all()
    )
    for row in hash_counts:
        books = db.query(Book).filter(Book.content_hash == row.content_hash).all()
        if len(books) > 1:
            hash_dupes.append(books)

    hash_dupes = _deduplicate_groups(hash_dupes, dismissed)

    # Strategy 2: same ISBN
    isbn_dupes: list[list[Book]] = []
    isbn_counts = (
        db.query(Book.isbn, func.count(Book.id).label("cnt"))
        .filter(Book.isbn.isnot(None))
        .group_by(Book.isbn)
        .having(func.count(Book.id) > 1)
        .all()
    )
    for row in isbn_counts:
        books = db.query(Book).filter(Book.isbn == row.isbn).all()
        if len(books) > 1:
            isbn_dupes.append(books)

    isbn_dupes = _deduplicate_groups(isbn_dupes, dismissed)

    # Collect IDs already found by exact strategies to avoid redundancy
    already_paired: set[frozenset[int]] = set()
    for group in hash_dupes + isbn_dupes:
        ids = [b.id for b in group]
        for a, b in itertools.combinations(ids, 2):
            already_paired.add(frozenset([a, b]))

    # Strategy 3: same author + series + series_index (different editions)
    series_dupes: list[list[Book]] = []
    series_counts = (
        db.query(
            func.lower(Book.author),
            func.lower(Book.series),
            Book.series_index,
            func.count(Book.id).label("cnt"),
        )
        .filter(Book.author.isnot(None), Book.author != "")
        .filter(Book.series.isnot(None), Book.series != "")
        .filter(Book.series_index.isnot(None))
        .group_by(func.lower(Book.author), func.lower(Book.series), Book.series_index)
        .having(func.count(Book.id) > 1)
        .all()
    )
    for row in series_counts:
        books = (
            db.query(Book)
            .filter(
                func.lower(Book.author) == row[0],
                func.lower(Book.series) == row[1],
                Book.series_index == row[2],
            )
            .all()
        )
        if len(books) > 1:
            series_dupes.append(books)

    series_dupes = _deduplicate_groups(series_dupes, dismissed)
    for group in series_dupes:
        ids = [b.id for b in group]
        for a, b in itertools.combinations(ids, 2):
            already_paired.add(frozenset([a, b]))

    # Strategy 4: similar title+author (skip pairs with different series_index)
    title_dupes: list[list[Book]] = []
    all_books = db.query(Book).all()

    author_groups: dict[str, list[Book]] = {}
    for book in all_books:
        key = (book.author or "").strip().lower()
        author_groups.setdefault(key, []).append(book)

    for _, group in author_groups.items():
        if len(group) < 2:
            continue
        pair_clusters: list[set[int]] = []

        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pair = frozenset([a.id, b.id])
                if pair in dismissed or pair in already_paired:
                    continue
                # Skip pairs that are clearly different volumes
                if (a.series_index is not None and b.series_index is not None
                        and a.series_index != b.series_index):
                    continue
                ratio = difflib.SequenceMatcher(
                    None,
                    a.title.lower().strip(),
                    b.title.lower().strip(),
                ).ratio()
                if ratio > 0.85:
                    merged = False
                    for cluster in pair_clusters:
                        if a.id in cluster or b.id in cluster:
                            cluster.add(a.id)
                            cluster.add(b.id)
                            merged = True
                            break
                    if not merged:
                        pair_clusters.append({a.id, b.id})

        book_index = {b.id: b for b in group}
        for cluster in pair_clusters:
            cluster_books = [book_index[bid] for bid in cluster if bid in book_index]
            if len(cluster_books) > 1:
                title_dupes.append(cluster_books)

    # Build response
    groups: list[DuplicateGroup] = []
    for group in hash_dupes:
        groups.append(DuplicateGroup(
            group_id=str(uuid.uuid4()),
            match_reason="content_hash",
            books=[_book_to_out(b) for b in group],
        ))
    for group in isbn_dupes:
        groups.append(DuplicateGroup(
            group_id=str(uuid.uuid4()),
            match_reason="isbn",
            books=[_book_to_out(b) for b in group],
        ))
    for group in series_dupes:
        groups.append(DuplicateGroup(
            group_id=str(uuid.uuid4()),
            match_reason="same_series_volume",
            books=[_book_to_out(b) for b in group],
        ))
    for group in title_dupes:
        groups.append(DuplicateGroup(
            group_id=str(uuid.uuid4()),
            match_reason="similar_title",
            books=[_book_to_out(b) for b in group],
        ))

    return DuplicatesResponse(groups=groups)


# ── POST /admin/duplicates/merge ──────────────────────────────────────────────

@router.post("/admin/duplicates/merge")
def merge_duplicates(
    body: MergeBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    keep = db.get(Book, body.keep_id)
    if not keep:
        raise HTTPException(status_code=404, detail=f"Book {body.keep_id} not found")

    if not body.remove_ids:
        raise HTTPException(status_code=400, detail="remove_ids must not be empty")

    if body.keep_id in body.remove_ids:
        raise HTTPException(status_code=400, detail="keep_id must not appear in remove_ids")

    merged_count = 0

    for remove_id in body.remove_ids:
        remove = db.get(Book, remove_id)
        if not remove:
            continue

        # Move BookFile rows to keep
        for bf in list(remove.files):
            bf.book_id = keep.id
        db.flush()

        # Copy tags not already present on keep
        existing_tags = {t.tag for t in keep.tags}
        for tag in list(remove.tags):
            if tag.tag not in existing_tags:
                new_tag = BookTag(book_id=keep.id, tag=tag.tag, source=tag.source)
                db.add(new_tag)
                existing_tags.add(tag.tag)

        # Copy library memberships not already present
        keep_lib_ids = {lib.id for lib in keep.libraries}
        for lib in list(remove.libraries):
            if lib.id not in keep_lib_ids:
                keep.libraries.append(lib)
                keep_lib_ids.add(lib.id)

        # Handle UserBookStatus: keep the entry with higher progress_pct
        remove_statuses = db.query(UserBookStatus).filter(UserBookStatus.book_id == remove_id).all()
        for remove_status in remove_statuses:
            keep_status = (
                db.query(UserBookStatus)
                .filter_by(user_id=remove_status.user_id, book_id=body.keep_id)
                .first()
            )
            if keep_status is None:
                # No existing status for this user on keep — reassign
                remove_status.book_id = body.keep_id
            else:
                # Both exist — keep the one with higher progress_pct
                remove_pct = remove_status.progress_pct or 0.0
                keep_pct = keep_status.progress_pct or 0.0
                if remove_pct > keep_pct:
                    keep_status.progress_pct = remove_pct
                    keep_status.status = remove_status.status
                    keep_status.cfi = remove_status.cfi
                # Remove the duplicate status row
                db.delete(remove_status)

        db.flush()
        db.delete(remove)
        db.flush()
        merged_count += 1

    db.commit()
    db.refresh(keep)

    audit(
        db,
        "books.duplicates_merged",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="book",
        resource_id=keep.id,
        resource_title=keep.title,
        details={"kept_id": body.keep_id, "removed_ids": body.remove_ids},
    )

    return {"merged": merged_count, "kept_id": body.keep_id}


# ── POST /admin/duplicates/dismiss ────────────────────────────────────────────

@router.post("/admin/duplicates/dismiss")
def dismiss_duplicates(
    body: DismissBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    if len(body.book_ids) < 2:
        raise HTTPException(status_code=400, detail="At least two book_ids required to dismiss a group")

    stored = 0
    for a, b in itertools.combinations(sorted(body.book_ids), 2):
        # Skip if already dismissed
        existing = (
            db.query(DuplicateDismissal)
            .filter_by(book_id_a=a, book_id_b=b)
            .first()
        )
        if existing:
            continue
        db.add(DuplicateDismissal(book_id_a=a, book_id_b=b))
        stored += 1

    db.commit()
    return {"dismissed": stored}
