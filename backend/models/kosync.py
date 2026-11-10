from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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


class KOSyncBookStats(Base):
    """The latest per-book stats snapshot a reader uploaded, one row per
    (Tome user, device, document).

    CrossInk keeps lifetime counters per book and uploads them whenever they
    change. Its reading sessions carry no page counts, so these counters are
    the only record of how many pages were read on such a reader. Each upload
    replaces the row, also with lower values: a reset or a restored card is
    the reader's current truth, not a counter to maximise.

    ``pages`` (forward page turns) and ``seconds`` (active reading time) are
    the reader's own pair: Tome divides one by the other for pace and never
    mixes either with its session time. ``snapshot`` keeps the whole item as
    sent. Like clippings, a row hangs off the document, not a book: it counts
    for the book ``KOSyncDocumentMap`` links its document to at read time, so a
    link made or changed later applies without anything being sent again.
    """
    __tablename__ = "kosync_book_stats"
    __table_args__ = (UniqueConstraint("user_id", "device_id", "document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # lower-case hex
    pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # When the reader sent the snapshot this row holds (the server's clock for
    # a live upload, the export's time for an imported one): the start of the
    # window the next snapshot's page growth is attributed over.
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Page attribution (services/kosync_stats.py, "page attribution"). The
    # counter's growth between two live snapshots goes to the reader's sessions
    # Tome received in between. ``counter_epoch`` goes up on every reset (a lower counter);
    # a session's receipt records the epoch it was attributed in, so pages
    # given before a reset are never subtracted from the counter after it.
    # ``pages_attributed`` is a ledger of pages handed out in this epoch (not
    # lowered when a session is deleted; the all-time figures use the sessions
    # that still exist). ``pages_pending`` is growth of a snapshot that found
    # no session (the session upload failed); it is given out at the next
    # snapshot. ``pending_since`` is when the window it belongs to began.
    counter_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pages_attributed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pages_pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pending_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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
    # When Tome first stored this session (server clock). A snapshot's page
    # growth goes to sessions received since the previous snapshot: the
    # reader uploads its sessions before its snapshots, so arrival order is
    # reliable where the reader's own clock may not be. None only on rows
    # from before the column existed (the startup migration fills it).
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The ``KOSyncBookStats.counter_epoch`` in which this session was given
    # pages from the reader's counter; None while it has none.
    pages_epoch: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The session's ``duration_seconds`` when it last took pages. A sitting
    # that goes on past a sync grows; only the growth since then weighs in
    # the next window.
    attributed_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    reading_session: Mapped[Optional["ReadingSession"]] = relationship("ReadingSession")  # type: ignore[name-defined]


class KOSyncDocumentMeta(Base):
    """What a reader said about one of its files: name, title, author.

    KOSync identifies a file by a hash only. Readers that also send document
    metadata with a progress push (KOReader's optional metadata field, used by
    CrossInk's "Send Document Metadata") tell Tome which book that hash is,
    which is what lets a sideloaded file be matched to a library book or get a
    book record of its own.
    """
    __tablename__ = "kosync_document_meta"
    __table_args__ = (UniqueConstraint("user_id", "document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    isbn: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    series: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    series_index: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The user deleted the file-less book made for this file: do not make it
    # again on the next sync. Linking the document by hand lifts it.
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class KOSyncClipping(Base):
    """A clipping (saved passage) a CrossInk reader synced, one row per id.

    CrossInk syncs clippings both ways over its extended KOSync API: it
    uploads the ones made on the device and restores the others from the
    server, so each row keeps the reader's fields as sent (page and word
    positions are only meaningful to the reader; ``text`` and ``para`` are the
    portable part). The reader names a clipping by its own ``clip_id``,
    unique per (Tome user, document).

    A deleted clipping stays as a tombstone (``deleted``; text and note are
    cleared) so a reader that syncs later learns about the deletion, and a
    deleted id is never brought back by a late upload. ``revision`` is a
    server-side counter, unique across the table, that only grows: readers
    page through changes with it as their cursor, because device clocks
    drift. ``updated_at`` is server epoch seconds, for the legacy ``since``
    queries.

    Rows hang off the document, not a book: Tome shows a clipping on the book
    that ``KOSyncDocumentMap`` links its document to, at read time, so a link
    made or changed later applies without anything being sent again. These
    are never sent to KOReader devices or drawn by the web reader; they have
    no KOReader position.
    """
    __tablename__ = "kosync_clippings"
    __table_args__ = (UniqueConstraint("user_id", "document", "clip_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # lower-case hex
    clip_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spine: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_word: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_word: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    para: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chapter: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # epoch seconds, device clock
    layout_signature: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)  # epoch seconds, server clock
