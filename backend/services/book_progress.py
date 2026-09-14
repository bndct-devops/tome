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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.tome_sync import PositionHistory, TomeSyncPosition
from backend.models.user_book_status import UserBookStatus

# Position history: record a new entry only when the position moved at least
# this much (or the locator string changed) — the device heartbeat re-PUTs
# every few pages and would otherwise fill the log with near-duplicates.
HISTORY_MIN_DELTA = 0.002
# Newest entries kept per (user, book); pruned on insert.
HISTORY_KEEP = 40


def _record_history(
    db: Session, *, user_id: int, book_id: int,
    percentage: float, progress: Optional[str], device: Optional[str],
) -> None:
    db.add(PositionHistory(
        user_id=user_id, book_id=book_id,
        percentage=percentage, progress=progress, device=device,
    ))
    # Prune beyond the cap — ids beat created_at for same-second inserts. The
    # server session is autoflush=False, so flush first or the query never sees
    # the row just added and the cap drifts to HISTORY_KEEP + 1.
    db.flush()
    stale = (
        db.query(PositionHistory.id)
        .filter(PositionHistory.user_id == user_id, PositionHistory.book_id == book_id)
        .order_by(PositionHistory.id.desc())
        .offset(HISTORY_KEEP)
        .all()
    )
    if stale:
        db.query(PositionHistory).filter(
            PositionHistory.id.in_([s.id for s in stale])
        ).delete(synchronize_session=False)


def upsert_position(
    db: Session,
    *,
    user_id: int,
    book_id: int,
    percentage: float,
    progress: Optional[str],
    device: Optional[str],
) -> TomeSyncPosition:
    """Insert-or-update the single (user, book) reading-position row.

    Both the device heartbeat and the web reader's autosave write positions;
    with a UNIQUE(user_id, book_id) constraint in place, a losing concurrent
    INSERT raises IntegrityError, which we absorb (via a SAVEPOINT so the
    caller's other pending changes survive) and retry as an UPDATE. The two
    writers then converge on one row instead of silently forking into two.
    Does not commit — the caller owns the transaction.
    """
    row = (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == user_id, TomeSyncPosition.book_id == book_id)
        .first()
    )
    if row is None:
        row = TomeSyncPosition(
            user_id=user_id, book_id=book_id,
            percentage=percentage, progress=progress, device=device,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            _record_history(db, user_id=user_id, book_id=book_id,
                            percentage=percentage, progress=progress, device=device)
            return row
        except IntegrityError:
            # Another writer created the row between our SELECT and INSERT.
            row = (
                db.query(TomeSyncPosition)
                .filter(TomeSyncPosition.user_id == user_id,
                        TomeSyncPosition.book_id == book_id)
                .first()
            )
            if row is None:
                raise

    moved = (
        abs(percentage - (row.percentage or 0.0)) >= HISTORY_MIN_DELTA
        or (progress or "") != (row.progress or "")
    )
    row.percentage = percentage
    row.progress = progress
    row.device = device
    row.updated_at = datetime.utcnow()
    if moved:
        _record_history(db, user_id=user_id, book_id=book_id,
                        percentage=percentage, progress=progress, device=device)
    return row


def reset_hardcover_read_state(row: UserBookStatus) -> None:
    """Forget the per-read Hardcover sync snapshot when a book leaves "read".

    ``hardcover_synced_pct`` is forward-only within one read-through (see
    ``hardcover_sync.needs_sync``) and ``hardcover_read_id`` points at the
    read entry that progress is written to. Neither is meaningful once the
    finished read is over: left in place, a re-read never out-runs the
    ``1.0`` snapshot, and any progress that did get through would land on
    the *completed* entry. Clearing both lets the next push start from zero
    and adopt (or open) the read entry that is currently in progress.
    """
    row.hardcover_synced_pct = None
    row.hardcover_read_id = None


def restart_reading(db: Session, row: UserBookStatus) -> None:
    """A finished book explicitly set back to "reading" starts over.

    Only the live bookmark is reset - progress, the resume CFI and the synced
    device position - so the re-read begins at page one instead of resuming
    at the last page, which the sticky rule would immediately re-finish.
    History is untouched: sessions, position history and the finished read
    entry on Hardcover all stay. Does not commit.
    """
    row.progress_pct = None
    row.cfi = None
    row.finished_at = None
    clear_position(db, user_id=row.user_id, book_id=row.book_id)
    reset_hardcover_read_state(row)


def clear_position(db: Session, *, user_id: int, book_id: int) -> None:
    """Drop the synced reading position for a user+book.

    Called when the web explicitly resets a book to "unread": otherwise the
    stale position row survives, the device re-pulls it on open, and the reset
    un-does itself. Does not commit — the caller owns the transaction.
    """
    db.query(TomeSyncPosition).filter(
        TomeSyncPosition.user_id == user_id,
        TomeSyncPosition.book_id == book_id,
    ).delete(synchronize_session=False)


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
        if finished:
            _nudge_hardcover()
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
        _nudge_hardcover()
    elif status_row.status in ("unread", "want_to_read") and pct > 0:
        # First real progress self-promotes a queued (want_to_read) book too.
        status_row.status = "reading"

    return status_row


def _nudge_hardcover() -> None:
    """Finishing a book is the socially meaningful event — ask the Hardcover
    sync worker for a near-term (debounced) push instead of waiting out the
    batch interval. Lazy import keeps this module dependency-light."""
    from backend.services.hardcover_sync import nudge
    nudge()
