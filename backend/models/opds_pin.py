"""Model for OPDS PINs — short app-specific passwords for e-reader OPDS access."""
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

# Characters excluded from PIN alphabet: 0, o, l, 1 (ambiguous on e-reader screens)
_PIN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_PIN_LENGTH = 6


class OpdsPin(Base):
    __tablename__ = "opds_pins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hashed_pin: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="KOReader")
    pin_preview: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]

    @staticmethod
    def generate() -> str:
        """Generate a random 6-character lowercase alphanumeric PIN (no ambiguous chars)."""
        return "".join(secrets.choice(_PIN_ALPHABET) for _ in range(_PIN_LENGTH))

    @staticmethod
    def make_preview(pin: str) -> str:
        """Return first 3 chars + '...' as a non-secret preview."""
        return pin[:3] + "..."
