"""A native client (the iOS app) signed in as a user.

Every login gets a row: a native client that identifies itself (`kind` "app")
and a web browser (`kind` "web", named from its User-Agent). The JWT carries
the row's `token_id` as `jti` and no expiry; every request looks the row up,
so revoking the row signs that device out on its next call.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class ClientDevice(Base):
    __tablename__ = "client_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Random id embedded in the JWT as `jti`; never the token itself.
    token_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    app_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # "app" (the Tome app) or "web" (a browser sign-in).
    kind: Mapped[str] = mapped_column(String(16), default="app", server_default="app", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
