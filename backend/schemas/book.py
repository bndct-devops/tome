from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class BookFileOut(BaseModel):
    id: int
    format: str
    filename: str | None = None
    file_size: Optional[int] = None
    added_at: datetime

    class Config:
        from_attributes = True


class BookTagOut(BaseModel):
    id: int
    tag: str
    source: Optional[str] = None

    class Config:
        from_attributes = True


class BookOut(BaseModel):
    id: int
    title: str
    subtitle: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    year: Optional[int] = None
    language: Optional[str] = None
    word_count: Optional[int] = None
    status: str
    content_type: str
    cover_path: Optional[str] = None
    added_at: datetime
    files: list[BookFileOut] = []
    tags: list[BookTagOut] = []
    library_ids: list[int] = []
    book_type_id: Optional[int] = None
    # Only populated by GET /books?group_by_series=true — number of volumes
    # in this book's series that matched the active filters, and the IDs of
    # the next volumes with covers (for the stacked-card fan effect).
    series_count: Optional[int] = None
    stack_cover_ids: Optional[list[int]] = None

    @classmethod
    def from_orm_with_libraries(cls, book):
        obj = cls.model_validate(book)
        obj.library_ids = [lib.id for lib in book.libraries]
        return obj

    class Config:
        from_attributes = True


class BookDetailOut(BookOut):
    isbn: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    content_hash: Optional[str] = None
    added_by: Optional[int] = None
    updated_at: datetime
    # Wishlist: populated on upload/ingest when the new book matches open wishes.
    # list of wish IDs (not book IDs) — frontend uses this to prompt "Fulfill N wishes?".
    # None means matcher was not run or no matches found.
    matched_wish_ids: Optional[list[int]] = None

    class Config:
        from_attributes = True


class BookUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    isbn: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    year: Optional[int] = None
    tags: Optional[list[str]] = None  # if set, replace all tags
    book_type_id: Optional[int] = None
    content_type: Optional[str] = None


class MetadataCandidateOut(BaseModel):
    source: str
    source_id: str
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    isbn: Optional[str] = None
    language: Optional[str] = None
    tags: list[str] = []
    series: Optional[str] = None
    series_index: Optional[float] = None


class ApplyMetadataRequest(BaseModel):
    cover_url: Optional[str] = None   # if set, download and replace cover
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[int] = None
    language: Optional[str] = None
    isbn: Optional[str] = None
    tags: Optional[list[str]] = None  # if set, replace all tags
    series: Optional[str] = None
    series_index: Optional[float] = None


class ScanResultOut(BaseModel):
    found: int
    added: int
    skipped: int
    duplicates: int
    errors: int
    error_details: list[str] = []
