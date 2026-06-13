"""ReadingGoal model — per-user reading goals.

Kinds follow the pattern {metric}_per_{period}:
  metric ∈ books | minutes | pages
  period ∈ day | week | month | year
Curated set lives in backend/api/goals.py:ALLOWED_KINDS.

A goal optionally targets a single book type (book_type_id), so "20 books
this year" and "20 manga this year" can coexist. NULL means all books.
Uniqueness per (user, kind, book_type_id) is enforced in the API — SQLite
treats NULLs as distinct, so a DB unique constraint can't cover the
all-books case.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class ReadingGoal(Base):
    __tablename__ = "reading_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = all books; set = only books of this type count toward the goal.
    book_type_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("book_types.id", ondelete="SET NULL"),
        nullable=True,
    )
    # UTC start of the period window for which a goal_reached notification was
    # already created. Resets naturally when the window rolls over; cleared on
    # target change so a raised target can notify again.
    notified_window_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
