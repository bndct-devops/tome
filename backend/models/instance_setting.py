"""Instance-wide key/value settings (operator decisions, not per-user).

First use: the telemetry consent record. Kept generic so other one-row
settings (an instance id, a last-run timestamp) have a home without each
growing a table.
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class InstanceSetting(Base):
    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
