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


class KOSyncDeviceStats(Base):
    """Latest all-time stats snapshot a reader uploaded, one row per device.

    Firmware that speaks the CrossPoint Sync stats API (CrossInk 2.0+) sends
    its lifetime counters on every sync; each upload replaces the previous
    one. Tome keeps the raw snapshot (it also names the device for the
    sessions that follow) but shows nothing from it yet.
    """
    __tablename__ = "kosync_device_stats"
    __table_args__ = (UniqueConstraint("user_id", "device_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class KOSyncDailyStats(Base):
    """Reading seconds per local calendar day, per device.

    The reader stops re-sending a day once Tome acknowledges it and later
    deletes old months from its own storage, so an acknowledged day has to be
    kept here. ``seconds`` is the device's running total for that day; a
    repeat keeps the larger value.
    """
    __tablename__ = "kosync_daily_stats"
    __table_args__ = (UniqueConstraint("user_id", "device_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD, device-local
    seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class KOSyncSessionReceipt(Base):
    """What a reader last reported for one of its reading sessions.

    The reader sends the same session again as the sitting goes on, and again
    after a lost reply or a restored SD card. Comparing with what it reported
    last time, instead of with the ``ReadingSession`` row, keeps the user's
    own changes in place: a session they trimmed only grows by what is new,
    and one they deleted (``reading_session_id`` is NULL then) stays deleted.

    Ids are the reader's, unique per Tome user. ``document`` is the KOSync
    hash of the file, which lets a session that arrived before its book was
    linked attach to the book later.
    """
    __tablename__ = "kosync_session_receipts"
    __table_args__ = (UniqueConstraint("user_id", "session_uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    reading_session_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("reading_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    reading_session: Mapped[Optional["ReadingSession"]] = relationship("ReadingSession")  # type: ignore[name-defined]
