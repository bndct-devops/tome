from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # Per-user rating in half-star steps (1.0–5.0) + optional free-text review.
    # NULL = unrated. Declared Float but the physical SQLite column keeps its
    # original INTEGER declaration: NUMERIC affinity stores 4.5 as REAL
    # losslessly, and legacy whole-star rows read back as ints that compare/hash
    # equal to their float forms — no data migration, mixed values coexist.
    # Half-step validation lives in the endpoints (users.py, series.py,
    # tome_sync.py).
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Hardcover sync state (per user+book). synced_* is the snapshot of what was
    # last pushed successfully — the worker is a stateless reconciler that syncs
    # iff current values differ from the snapshot, so nothing is ever lost to a
    # crashed cycle. user_book_id / read_id are Hardcover's own row ids, captured
    # after the first write to avoid a lookup per push.
    hardcover_user_book_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hardcover_read_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hardcover_synced_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hardcover_synced_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hardcover_synced_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    hardcover_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    hardcover_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hardcover_fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # True only when Tome itself inserted the Hardcover entry this row points
    # at. False means the entry was already on the user's profile and Tome
    # merely adopted it — their own reading history, which Tome may write to
    # but must never delete, and whose finished reads it must not rewrite.
    # Legacy rows default to False on purpose: origin cannot be recovered
    # afterwards, so an unknown entry is treated as the user's (#227).
    hardcover_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When the book was marked read. updated_at is NOT a finish date — it moves
    # on every rating/review/CFI write (onupdate), so it must not be displayed
    # as one. Stamped by apply_progress_to_status / the status endpoint on the
    # transition into "read"; cleared when the book is un-finished.
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
