"""Where the reading companion is switched on.

Nothing AI reads a book on its own: the automatic triggers (a synced position,
a book marked read) act only where the user switched the companion on. The
switch is per series (one row carries to every volume) or per standalone book,
keyed by ``digest.series_key``. ``start_book_id`` / ``start_chapter_idx``
record where the user was when they switched it on: automatic reads never go
back before that point in that book (an explicit "Read the missing chapters"
still can). A book that already had digests starts at 0, so the read
continues where it stopped.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AICompanionOptIn(Base):
    __tablename__ = "ai_companion_optin"
    __table_args__ = (UniqueConstraint("user_id", "scope_key", name="uq_ai_companion_optin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # ``digest.series_key(book)``: the series name normalised, or ``book:{id}``.
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_book_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    start_chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
