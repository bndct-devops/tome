"""Nightlamp link (the "platform"): one row per linked Tome user.

Tome pushes the user's daily reading totals to the Nightlamp service named by
``TOME_PLATFORM_URL`` (see backend/services/platform_push.py). The push token
is replayed on every push, so it is stored Fernet-encrypted
(backend/core/crypto.py), never hashed. ``revoked_at`` is set when Nightlamp
answers 401; pushing stops until the user links again.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class PlatformLink(Base):
    __tablename__ = "platform_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    push_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    share_titles: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_push_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_push_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The last local day (in ``timezone``) fully pushed; None = never pushed.
    pushed_through: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PlatformCoverSent(Base):
    """A cover goes to Nightlamp once per book per user; "Resync everything"
    clears these rows so the covers go again."""
    __tablename__ = "platform_cover_sent"
    __table_args__ = (UniqueConstraint("user_id", "book_key", name="uq_platform_cover_sent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_key: Mapped[str] = mapped_column(String(64), nullable=False)
