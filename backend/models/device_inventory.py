"""Which Tome books are on each of a user's KOReader devices.

The TomeSync plugin sends the full list of Tome book ids it holds on the
device (its book map, files that still exist) on launch, Sync now, after
downloads and when WiFi comes up. Each upload REPLACES the device's set, so a
book deleted on the device (in KOReader, over USB, by Calibre) drops out on
the next sync without needing a delete event KOReader does not have.

A device is identified the way the rest of TomeSync identifies it: the
user plus the plugin's device name (Settings > Device name, default the
device model). Renaming the device moves its inventory (see
`ko_stats_import.rename_device`).
"""
from datetime import datetime
from typing import List

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class DeviceInventory(Base):
    __tablename__ = "device_inventories"
    __table_args__ = (UniqueConstraint("user_id", "device", name="uq_device_inventory_user_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device: Mapped[str] = mapped_column(String(128), nullable=False)
    # When the device last sent its list; the UI marks a list as stale from it.
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    plugin_build: Mapped[int | None] = mapped_column(Integer, nullable=True)

    books: Mapped[List["DeviceBook"]] = relationship(
        "DeviceBook", cascade="all, delete-orphan", passive_deletes=True,
        back_populates="inventory",
    )


class DeviceBook(Base):
    __tablename__ = "device_books"
    __table_args__ = (UniqueConstraint("inventory_id", "book_id", name="uq_device_book"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device_inventories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )

    inventory: Mapped["DeviceInventory"] = relationship("DeviceInventory", back_populates="books")
