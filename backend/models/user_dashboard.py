"""Per-user stats dashboard state — the customisable board layouts.

One row per user holding the full dashboard JSON (tabs, tiles, layouts,
per-tile config, view settings). The shape is owned by the frontend; the
backend treats it as an opaque blob so widget/catalog changes never need
a migration.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class UserDashboard(Base):
    __tablename__ = "user_dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
