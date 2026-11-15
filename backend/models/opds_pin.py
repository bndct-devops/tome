"""Model for OPDS PINs — short app-specific passwords for e-reader OPDS access."""
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

# Characters excluded from PIN alphabet: 0, o, l, 1 (ambiguous on e-reader screens)
_PIN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# 10 characters from 31 is about 49 bits: out of reach for offline guessing
# against the bcrypt hash, and still quick to type on an e-reader keyboard.
_PIN_LENGTH = 10


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
        """Generate a random 10-character lowercase alphanumeric PIN (no ambiguous chars)."""
        return "".join(secrets.choice(_PIN_ALPHABET) for _ in range(_PIN_LENGTH))

    @staticmethod
    def make_preview(pin: str) -> str:
        """Return the first character + '...' as a non-secret preview. One
        character tells PINs apart in the list without narrowing a guess."""
        return pin[:1] + "..."
