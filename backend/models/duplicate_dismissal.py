from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class DuplicateDismissal(Base):
    """Stores dismissed duplicate book pairs so they are excluded from future results."""

    __tablename__ = "duplicate_dismissals"
    __table_args__ = (UniqueConstraint("book_id_a", "book_id_b"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id_a: Mapped[int] = mapped_column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id_b: Mapped[int] = mapped_column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
