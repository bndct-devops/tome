from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class KOSyncUser(Base):
    __tablename__ = "kosync_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    userkey: Mapped[str] = mapped_column(String(32), nullable=False)  # MD5 hash from KOReader
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    progress: Mapped[list["KOSyncProgress"]] = relationship(
        "KOSyncProgress", back_populates="kosync_user", cascade="all, delete-orphan"
    )


class KOSyncProgress(Base):
    __tablename__ = "kosync_progress"
    __table_args__ = (UniqueConstraint("user_id", "document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kosync_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    progress: Mapped[str] = mapped_column(Text, nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)

    kosync_user: Mapped["KOSyncUser"] = relationship("KOSyncUser", back_populates="progress")


class KOSyncDocumentMap(Base):
    """Maps a KOReader document hash to a Tome book, per user."""
    __tablename__ = "kosync_document_map"
    __table_args__ = (UniqueConstraint("tome_user_id", "document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tome_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )


class ReadingHistory(Base):
    """Append-only log of every KOSync progress push, for stats."""
    __tablename__ = "reading_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document: Mapped[str] = mapped_column(String(64), nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class OPDSPendingLink(Base):
    """Records an OPDS download so the next unknown KOSync push can be auto-linked."""
    __tablename__ = "opds_pending_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
