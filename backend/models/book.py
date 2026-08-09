from pathlib import Path
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.library import BookType, Library


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(Text)
    series: Mapped[Optional[str]] = mapped_column(Text)
    series_index: Mapped[Optional[float]] = mapped_column(Float)
    isbn: Mapped[Optional[str]] = mapped_column(String(32))
    publisher: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(16))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    # Intrinsic, device-independent word count parsed from the EPUB text at
    # ingest (or backfilled by the admin word-count job). NULL for PDF/CBZ or
    # not-yet-parsed books. Feeds words-read / true-WPM stats (Phase 4).
    word_count: Mapped[Optional[int]] = mapped_column(Integer)
    # Intrinsic page count for PDF/CBZ/CBR only (fixed-layout formats). EPUB
    # deliberately stays NULL — a reflowable page count is device/font-specific
    # and must never be stored as a book property (KOReader page-stats carry
    # their own total_pages per row for that).
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    # When chapter extraction last RAN for this book (found chapters or not).
    # Distinguishes "never tried" from "tried, no usable TOC" — without it, a
    # TOC-less EPUB re-queues in the backfill forever, since pending can't be
    # inferred from BookChapter rows it will never have.
    chapters_extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Hardcover identity — book-level because a title's Hardcover book/edition is
    # user-independent. Matched lazily by the sync worker (ISBN first, strict
    # title+author search as fallback). match_method "none" + matched_at set =
    # "tried and failed"; re-tried only after a metadata edit bumps updated_at.
    # hardcover_pages is the matched edition's page count, captured at match time
    # — Tome stores no page counts of its own, and progress→pages math must use
    # the edition's own pagination anyway.
    hardcover_book_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    hardcover_edition_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hardcover_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # isbn13 | isbn10 | search | manual (user-pinned, never auto-cleared) |
    # none (tried+failed, auto-retryable) | excluded (user said never) |
    # NULL (not attempted yet — "pending" in the API)
    hardcover_match_method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    hardcover_matched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The matched record's URL slug — lets the UI link to hardcover.app/books/{slug}
    # so users can AUDIT what their books matched to and re-match wrong ones.
    hardcover_slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cover_path: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    book_type_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("book_types.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    content_type: Mapped[str] = mapped_column(String(16), default="volume", server_default="volume", nullable=False)
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    added_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    files: Mapped[list["BookFile"]] = relationship(
        "BookFile", back_populates="book", cascade="all, delete-orphan"
    )
    tags: Mapped[list["BookTag"]] = relationship(
        "BookTag", back_populates="book", cascade="all, delete-orphan"
    )
    libraries: Mapped[List["Library"]] = relationship(
        "Library",
        secondary="book_library",
        back_populates="books",
    )
    book_type: Mapped[Optional["BookType"]] = relationship(
        "BookType", back_populates="books", foreign_keys=[book_type_id]
    )

    @property
    def library_ids(self) -> list[int]:
        return [lib.id for lib in (self.libraries or [])]


class BookFile(Base):
    __tablename__ = "book_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(Integer, ForeignKey("books.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)  # epub, pdf, cbz, mobi
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    book: Mapped["Book"] = relationship("Book", back_populates="files")

    @property
    def filename(self) -> str:
        return Path(self.file_path).name


class BookTag(Base):
    __tablename__ = "book_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(Integer, ForeignKey("books.id"), nullable=False)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(32))  # "google_books", "open_library", "user"

    book: Mapped["Book"] = relationship("Book", back_populates="tags")


class BookChapter(Base):
    """One TOC chapter with device-independent boundaries.

    Boundaries are stored as fraction-of-book (by word offset), NOT page
    numbers — pages are device/pagination-specific. Per-page dwell (KOReader
    page-stats, which carry their own total_pages) maps into these buckets at
    query time: fraction = page/total_pages. Extracted at ingest from the EPUB
    TOC (or backfilled by the admin word-count job); fixed-layout formats have
    no chapter map.
    """
    __tablename__ = "book_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)          # 0-based TOC order
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    start_fraction: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1
    end_fraction: Mapped[float] = mapped_column(Float, nullable=False)    # 0..1

    book: Mapped["Book"] = relationship("Book")
