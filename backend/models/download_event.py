from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class DownloadEvent(Base):
    """One row per file served to a non-admin user, across every download
    path (single / bulk ZIP / OPDS / TomeSync). Counted by
    backend/services/download_quota.py to enforce ``User.download_limit``.
    ``book_id`` is deliberately not a foreign key: deleting a book must not
    delete its rows, or the day's count would go backwards.
    """

    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
