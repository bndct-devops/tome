"""The sticky-completion progress→status rule, shared by every write path.

Used by the device position sync, the device session flush (TomeSync), and the
manual "Log session" endpoint. Before this helper each site carried its own
diverging copy — the tome_sync copies had an ``if/elif`` quirk where an unread
book synced straight to 100% became "reading" instead of "read" until the next
sync.

Rules:
- Completion is sticky: a "read" book is never un-finished by a later write
  (re-reads don't drag it back); only the resume CFI keeps tracking.
- ``pct >= 0.99`` finishes the book: status "read", progress pinned to 1.0,
  ``finished_at`` stamped (once) — even coming straight from "unread".
- Otherwise any positive progress moves an "unread" book to "reading".
- ``monotonic=True`` (session flush, manual log) only ever advances
  ``progress_pct``; ``monotonic=False`` (device position sync) tracks the
  reported position last-write-wins, downward included.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.user_book_status import UserBookStatus


def apply_progress_to_status(
    db: Session,
    *,
    user_id: int,
    book_id: int,
    pct: float,
    monotonic: bool = True,
    cfi: Optional[str] = None,
    status_row: Optional[UserBookStatus] = None,
) -> UserBookStatus:
    """Apply a progress report to the user's status row (creating it if needed).

    Does not commit — the caller owns the transaction.
    """
    if status_row is None:
        status_row = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user_id, UserBookStatus.book_id == book_id)
            .first()
        )

    if status_row is None:
        finished = pct >= 0.99
        status_row = UserBookStatus(
            user_id=user_id,
            book_id=book_id,
            status="read" if finished else ("reading" if pct > 0 else "unread"),
            progress_pct=1.0 if finished else pct,
            cfi=cfi,
            finished_at=datetime.utcnow() if finished else None,
        )
        db.add(status_row)
        return status_row

    # Resume position tracks the latest report even on finished books.
    if cfi is not None:
        status_row.cfi = cfi

    if status_row.status == "read":
        return status_row  # sticky — status and progress stay finished

    if monotonic:
        if pct > (status_row.progress_pct or 0):
            status_row.progress_pct = pct
    else:
        status_row.progress_pct = pct

    if pct >= 0.99:
        status_row.status = "read"
        status_row.progress_pct = 1.0
        status_row.finished_at = datetime.utcnow()
    elif status_row.status == "unread" and pct > 0:
        status_row.status = "reading"

    return status_row
