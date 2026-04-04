from pathlib import Path
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
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
    cover_path: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    book_type_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("book_types.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    content_type: Mapped[str] = mapped_column(String(16), default="volume", server_default="volume", nullable=False)
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
