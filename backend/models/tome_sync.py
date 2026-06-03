"""Models for TomeSync — custom KOReader plugin sync."""
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex digest of the plaintext key. Plaintext is never stored —
    # only returned at provision time. See backend/api/tome_sync.py.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # First ~11 chars of plaintext (e.g. "tk_a1b2c3d4") shown in the UI so users
    # can identify which device's key this is. Not a credential — too short to brute-force the rest.
    key_prefix: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="KOReader Plugin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]

    @staticmethod
    def generate() -> str:
        """Generate a new plaintext API key with tk_ prefix. Hash before storing."""
        return "tk_" + secrets.token_hex(20)  # tk_ + 40 hex chars = 43 chars total

    @staticmethod
    def hash_key(plaintext: str) -> str:
        import hashlib
        return hashlib.sha256(plaintext.encode()).hexdigest()


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


class Annotation(Base):
    """A highlight (and optional note) synced from KOReader, per user+book.

    Bidirectional across KOReader devices via Tome: `anchor` (KOReader's highlight
    start xPointer) is the identity — the same passage highlighted on two devices is
    one row. Conflicting edits resolve last-write-wins by `koreader_datetime_updated`.
    Deletes are recorded as `AnnotationTombstone` rows (this row is removed) so a
    stale device can't resurrect a deleted highlight on its next pull.
    """
    __tablename__ = "annotations"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", "anchor", name="uq_annotation_user_book_anchor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    anchor: Mapped[str] = mapped_column(String(512), nullable=False)  # KOReader pos0 (xPointer) — identity
    # pos1 (xPointer end). Stored so another device can reconstruct and *render* the
    # highlight, not just list its text. EPUB only; null for PDF (table positions).
    anchor_end: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    chapter: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    highlighted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # KOReader's own creation timestamp for the highlight (display ordering);
    # distinct from created_at, which is when Tome first stored it.
    koreader_datetime: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # KOReader's last-modification time — the last-write-wins key for edit conflicts.
    koreader_datetime_updated: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
    book: Mapped["Book"] = relationship("Book")  # type: ignore[name-defined]

    @property
    def effective_mtime(self) -> str:
        """The timestamp used for last-write-wins. Falls back to creation time."""
        return self.koreader_datetime_updated or self.koreader_datetime or ""


class AnnotationTombstone(Base):
    """Records that a highlight (by anchor) was deleted, so a stale device pulling
    later removes it locally instead of re-uploading and resurrecting it.

    `client_deleted_at` is the device's wall-clock at deletion — the LWW key compared
    against an incoming upsert's `koreader_datetime_updated`: a strictly-newer re-add
    wins and clears the tombstone (you re-highlighted the same passage on purpose).
    """
    __tablename__ = "annotation_tombstones"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", "anchor", name="uq_tombstone_user_book_anchor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    anchor: Mapped[str] = mapped_column(String(512), nullable=False)
    client_deleted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
