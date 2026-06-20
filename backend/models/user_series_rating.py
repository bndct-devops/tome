from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class UserSeriesRating(Base):
    """A user's rating/review of a whole series, keyed by series name.

    Stored once per (user, series) rather than copied onto each volume. A
    volume's *effective* rating is its own rating if set, otherwise this
    series rating (inherited). See backend/api/books.py for the COALESCE.
    """

    __tablename__ = "user_series_rating"
    __table_args__ = (UniqueConstraint("user_id", "series_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    series_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
