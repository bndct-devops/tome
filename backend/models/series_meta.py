from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class Arc(Base):
    __tablename__ = "arcs"
    __table_args__ = (UniqueConstraint("series_name", "name", name="uq_arc_series_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_index: Mapped[float] = mapped_column(Float, nullable=False)
    end_index: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SeriesMeta(Base):
    __tablename__ = "series_meta"

    id: Mapped[int] = mapped_column(primary_key=True)
    series_name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
