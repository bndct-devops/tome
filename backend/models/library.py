from datetime import datetime
from typing import Optional, List
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


# M2M join table — books ↔ libraries
book_library_table = Table(
    "book_library",
    Base.metadata,
    Column("book_id", Integer, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("library_id", Integer, ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True),
)

# M2M join table — users ↔ private libraries
library_users_table = Table(
    "library_users",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("library_id", Integer, ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True),
)


class BookType(Base):
    __tablename__ = "book_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    icon: Mapped[str] = mapped_column(String(64), nullable=False, default="BookOpen")
    color: Mapped[str] = mapped_column(String(32), nullable=False, default="blue")
    # The auto-created library for this type (set on first use)
    library_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    library: Mapped[Optional["Library"]] = relationship("Library", foreign_keys=[library_id])
    books: Mapped[List["Book"]] = relationship("Book", back_populates="book_type")


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(String(64), default="Library")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    default_book_type_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("book_types.id"), nullable=True)

    books: Mapped[List["Book"]] = relationship(
        "Book",
        secondary=book_library_table,
        back_populates="libraries",
    )
    assigned_users: Mapped[List["User"]] = relationship(
        "User",
        secondary=library_users_table,
    )


class SavedFilter(Base):
    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(String(64), default="Bookmark")
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ShareLink(Base):
    """An opt-in, tokenized, read-only public view of one shelf.

    HARD BOUNDARY (owner's explicit constraint): the public payload is
    metadata only — cover, title, author/series identity, tags, description,
    the owner's rating, and their highlights. No file paths, no downloads, no
    reader, no route from a share to any book content, ever. The public
    endpoint lives in its own router with an explicit whitelist serializer and
    zero imports from file-serving modules — the boundary is structural, not
    policy. Revoking deletes the row (the token dies).

    Three share targets, exactly one set per row: a shelf (saved_filter_id),
    a series (series_name), or a single book (book_id)."""
    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    saved_filter_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("saved_filters.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    series_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=True
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    shelf: Mapped[Optional["SavedFilter"]] = relationship("SavedFilter")
