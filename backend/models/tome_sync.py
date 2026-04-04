"""Models for TomeSync — custom KOReader plugin sync."""
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(44), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="KOReader Plugin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]

    @staticmethod
    def generate() -> str:
        """Generate a new API key with tk_ prefix."""
        return "tk_" + secrets.token_hex(20)  # tk_ + 40 hex chars = 43 chars total


class ReadingSession(Base):
    __tablename__ = "reading_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_start: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    progress_end: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pages_turned: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Client-generated UUID to prevent duplicates on retry
    session_uuid: Mapped[Optional[str]] = mapped_column(String(36), unique=True, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]


class TomeSyncPosition(Base):
    """Latest reading position per user+book, updated on every push."""
    __tablename__ = "tome_sync_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    progress: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # CFI or page ref
    percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
    book: Mapped["Book"] = relationship("Book")  # type: ignore[name-defined]
