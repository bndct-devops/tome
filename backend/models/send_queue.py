from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class SendQueueItem(Base):
    """A book queued from the web UI to be pulled onto a user's KOReader via the
    TomeSync plugin inbox (the "Send to KOReader" beta feature).

    Per-user, not per-device: all of a user's KOReader installs pull the same
    inbox. The plugin lists pending items, downloads them, and marks each
    delivered. Additive table only — nothing here alters existing schema, so it
    auto-creates via ``Base.metadata.create_all()`` with no migration. Gated by
    ``settings.send_to_koreader``.
    """

    __tablename__ = "send_queue_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # File pinned at enqueue time. If it's gone by delivery, the plugin falls
    # back to the book's best available file (so a deleted format never strands
    # a queued item).
    file_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("book_files.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    # NULL while pending; set when the plugin confirms it pulled the file.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
    book: Mapped["Book"] = relationship("Book")  # type: ignore[name-defined]
