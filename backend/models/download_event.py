from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class DownloadEvent(Base):
    """One row per served download.

    Used to enforce per-user daily download limits
    (``UserPermission.download_limit``) and to give a lightweight download
    history. ``book_id`` is intentionally not a foreign key so deleting a book
    never disturbs the count.
    """

    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    book_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
