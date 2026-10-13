"""Device inventories: which Tome books sit on each of a user's KOReader devices.

See `backend.models.device_inventory`. Everything here is per user: a user
only ever sees and filters by their own devices.
"""
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.device_inventory import DeviceBook, DeviceInventory

# A Kindle with a large library holds a few thousand books; the cap only
# guards the request size.
MAX_BOOK_IDS = 20000
DEVICE_NAME_MAX = 128


def clean_device(name: Optional[str]) -> str:
    return (name or "").strip()[:DEVICE_NAME_MAX]


def replace(db: Session, user, *, device: str, book_ids: Iterable[int],
            plugin_build: Optional[int] = None) -> dict:
    """Store `book_ids` as the complete set of Tome books on `device`.

    Ids the user cannot see (or that do not exist) are dropped, so the stored
    set never reveals or references a book outside the user's view. Returns
    {stored, ignored}.
    """
    from backend.core.permissions import book_visibility_filter

    device = clean_device(device)
    wanted = {int(i) for i in book_ids if isinstance(i, int) and i > 0}
    visible: set[int] = set()
    ids = sorted(wanted)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        visible.update(
            r[0] for r in db.query(Book.id).filter(
                Book.id.in_(chunk), Book.status == "active",
                book_visibility_filter(db, user),
            ).all()
        )

    inv = (
        db.query(DeviceInventory)
        .filter(DeviceInventory.user_id == user.id, DeviceInventory.device == device)
        .first()
    )
    if inv is None:
        inv = DeviceInventory(user_id=user.id, device=device)
        db.add(inv)
        db.flush()
    current = {
        r[0] for r in db.query(DeviceBook.book_id).filter(DeviceBook.inventory_id == inv.id).all()
    }
    gone = current - visible
    if gone:
        gone_ids = sorted(gone)
        for i in range(0, len(gone_ids), 900):
            db.query(DeviceBook).filter(
                DeviceBook.inventory_id == inv.id,
                DeviceBook.book_id.in_(gone_ids[i:i + 900]),
            ).delete(synchronize_session=False)
    for book_id in sorted(visible - current):
        db.add(DeviceBook(inventory_id=inv.id, book_id=book_id))
    inv.synced_at = datetime.utcnow()
    if plugin_build is not None:
        inv.plugin_build = plugin_build
    db.commit()
    return {"stored": len(visible), "ignored": len(wanted) - len(visible)}


def payload(db: Session, user) -> dict:
    """The user's devices, newest sync first, each with the ids of the books
    on it that the user can currently see."""
    from backend.core.permissions import book_visibility_filter

    invs = (
        db.query(DeviceInventory)
        .filter(DeviceInventory.user_id == user.id)
        .order_by(DeviceInventory.synced_at.desc())
        .all()
    )
    devices = []
    for inv in invs:
        ids = [
            r[0] for r in
            db.query(DeviceBook.book_id)
            .join(Book, Book.id == DeviceBook.book_id)
            .filter(
                DeviceBook.inventory_id == inv.id,
                Book.status == "active",
                book_visibility_filter(db, user),
            )
            .order_by(DeviceBook.book_id)
            .all()
        ]
        devices.append({
            "name": inv.device,
            "synced_at": inv.synced_at.isoformat() + "Z",
            "plugin_build": inv.plugin_build,
            "book_count": len(ids),
            "book_ids": ids,
        })
    return {"devices": devices}


def forget(db: Session, user, device: str) -> bool:
    inv = (
        db.query(DeviceInventory)
        .filter(DeviceInventory.user_id == user.id, DeviceInventory.device == clean_device(device))
        .first()
    )
    if inv is None:
        return False
    db.delete(inv)
    db.commit()
    return True


def rename(db: Session, user, *, from_device: str, to_device: str) -> bool:
    """Carry an inventory over to the device's new name. The list under the
    new name wins when both exist: it was sent by the device as it is now.
    Does not commit (runs inside the stats rename transaction)."""
    from_device, to_device = clean_device(from_device), clean_device(to_device)
    if not from_device or not to_device or from_device == to_device:
        return False
    old = (
        db.query(DeviceInventory)
        .filter(DeviceInventory.user_id == user.id, DeviceInventory.device == from_device)
        .first()
    )
    if old is None:
        return False
    taken = db.query(
        exists().where(
            (DeviceInventory.user_id == user.id) & (DeviceInventory.device == to_device)
        )
    ).scalar()
    if taken:
        db.delete(old)
    else:
        old.device = to_device
    return True


def split_devices(value: Optional[str]) -> list[str]:
    """A filter value names one or more devices, comma-separated
    (`on_device=Kindle,Boox`): one string per direction keeps it a plain
    URL / shelf parameter."""
    return [d for d in (clean_device(p) for p in (value or "").split(",")) if d]


def apply_filters(query, user, *, on_device: Optional[str], not_on_device: Optional[str]):
    """Every device in `on_device` must hold the book, none in `not_on_device`."""
    for name in split_devices(on_device):
        query = query.filter(filter_clause(user, name, on=True))
    for name in split_devices(not_on_device):
        query = query.filter(filter_clause(user, name, on=False))
    return query


def filter_clause(user, device: str, *, on: bool):
    """WHERE clause for the books list: on (or not on) the user's `device`."""
    present = (
        select(DeviceBook.id)
        .join(DeviceInventory, DeviceInventory.id == DeviceBook.inventory_id)
        .where(
            DeviceBook.book_id == Book.id,
            DeviceInventory.user_id == user.id,
            DeviceInventory.device == clean_device(device),
        )
        .exists()
    )
    return present if on else ~present
