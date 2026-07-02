from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class UserBookStatus(Base):
    __tablename__ = "user_book_status"
    __table_args__ = (UniqueConstraint("user_id", "book_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id: Mapped[int] = mapped_column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="unread", nullable=False)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cfi: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Per-user rating (1–5 stars) + optional free-text review. NULL = unrated.
    # Held here because this row is already the per-user-per-book record. The
    # optional hardcover_* columns are reserved so a future Hardcover sync is
    # additive — see docs/plans (ratings POC).
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # When the book was marked read. updated_at is NOT a finish date — it moves
    # on every rating/review/CFI write (onupdate), so it must not be displayed
    # as one. Stamped by apply_progress_to_status / the status endpoint on the
    # transition into "read"; cleared when the book is un-finished.
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
