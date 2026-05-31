"""Wish model — the core of the wishlist feature.

Each row represents a single member's want for a book or series.
Status: open | fulfilled | dismissed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User
    from backend.models.book import Book


class Wish(Base):
    __tablename__ = "wishes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # reserved for the detection plan; "wish" today, "follow" later
    kind: Mapped[str] = mapped_column(String(16), default="wish", nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)

    # Structured reference (from metadata search) — title is always present
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    # series set means "I want the whole series"; series_index narrows to a single volume
    series: Mapped[Optional[str]] = mapped_column(String(255))
    series_index: Mapped[Optional[float]] = mapped_column(Float)
    cover_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source: Mapped[Optional[str]] = mapped_column(String(32))   # google_books | open_library | hardcover | manual
    source_id: Mapped[Optional[str]] = mapped_column(String(128))
    isbn: Mapped[Optional[str]] = mapped_column(String(20))

    # Free-text note
    note: Mapped[Optional[str]] = mapped_column(Text)

    # Fulfilment linkage
    fulfilled_book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    fulfilled_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Suggestion hint — populated by the matcher before admin confirms
    suggested_book_ids: Mapped[Optional[str]] = mapped_column(
        Text, default=None
    )  # JSON-encoded list[int]; null until matcher fires

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # ── reserved for the detection plan (added now, unused) ──────────────────
    external_series_id: Mapped[Optional[str]] = mapped_column(String(128))
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    latest_known_index: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_id", name="uq_wish_user_source"),
        Index("ix_wish_status", "status"),
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="wishes"
    )
    fulfilled_book: Mapped[Optional["Book"]] = relationship(
        "Book", foreign_keys=[fulfilled_book_id]
    )
    fulfiller: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[fulfilled_by]
    )
