"""Models for importing KOReader's `statistics.sqlite3` into Tome.

Phase 2.1 of the stats-expansion plan. KOReader records every page's dwell time in
its own SQLite DB, going back to whenever the user started reading — well before Tome
existed. Importing it backfills the entire reading history and gives ground-truth,
idle-capped read time. See docs/plans/stats-expansion-plan.md.

Three tables:
- ``PageStat``         — the imported per-page dwell rows (mirror of KOReader ``page_stat_data``).
- ``StatsImport``      — per-device watermark so the plugin can sync incrementally.
- ``KoStatsBookMatch`` — cached KOReader-book → Tome-book resolution (the md5 join is dead
                         for plugin users; matching is by filename/fuzzy, so we cache it).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class PageStat(Base):
    """One page-dwell record imported from KOReader ``page_stat_data``.

    Stored raw (we keep ``total_pages`` per row, as KOReader does, so a later
    pagination change can still be rescaled). Idempotent on
    (user, book, page, start_time, device) — re-importing the same rows is a no-op,
    and the same page read on two devices in the same second stays two events.
    """
    __tablename__ = "ko_page_stats"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "book_id", "page", "start_time", "device",
            name="uq_ko_page_stat_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    # KOReader's page count *at the time the row was written* — needed to rescale to
    # the book's current pagination (KOReader's page_stat view does the same).
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # KOReader wall-clock, epoch seconds (page_stat_data.start_time).
    start_time: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Source device (whole statistics.sqlite3 is per-device, so this is per-import).
    device: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
    book: Mapped["Book"] = relationship("Book")  # type: ignore[name-defined]


class StatsImport(Base):
    """Per-user, per-device import watermark for incremental sync.

    The plugin asks "what's my last synced start_time for this device?" and only
    uploads newer page_stat rows. Server-side reconstruction is therefore cheap and
    resumable.
    """
    __tablename__ = "ko_stats_imports"
    __table_args__ = (
        UniqueConstraint("user_id", "device", name="uq_ko_stats_import_user_device"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_start_time_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class KoStatsBookMatch(Base):
    """Cached resolution of a KOReader book (identified by its partial md5) to a Tome book.

    The md5 join against ``kosync_document_map`` is dead for plugin users (those tables
    are empty), so matching is by filename (exact, for books still on the device) or
    fuzzy title+series+volume (the historical tail). We cache the result so we match
    once, reuse it, and so manual confirmations stick. ``book_id`` null = parked
    (unmatched / awaiting review), re-matched when the library grows.
    """
    __tablename__ = "ko_stats_book_matches"
    __table_args__ = (
        UniqueConstraint("user_id", "ko_md5", name="uq_ko_stats_match_user_md5"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ko_md5: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ko_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    ko_authors: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 'filename' (exact), 'fuzzy', 'manual', or 'none'
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    # 'matched' | 'review' | 'unmatched' — drives the manual-review UI.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unmatched")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    book: Mapped[Optional["Book"]] = relationship("Book")  # type: ignore[name-defined]
