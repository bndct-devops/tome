"""Reading stats uploaded by a KOSync reader (experimental).

Plain KOSync carries a position and nothing else. CrossInk firmware (2.0+)
additionally speaks the CrossPoint Sync stats API against any server that
answers it: lifetime counters per device, reading seconds per calendar day,
and measured reading sessions (book, start, end, active seconds). This module
is the storage half of Tome's side of that API; the routes live in
``backend/api/kosync_stats.py``.

What Tome does with each upload:

- sessions become ``ReadingSession`` rows, so the stats pages, the calendar
  and streaks pick them up like any other session. They carry time but no
  page counts and no progress.
- daily seconds and the lifetime snapshot are stored and not shown yet.
- per-book snapshots are stored, the latest per device and document
  (``KOSyncBookStats``). Their page counts are the only pages Tome learns
  about reading done on such a reader; they fill a book's all-time pages and
  pace (``reader_pages``), never a day, a session or any other window.

An acknowledgment is a promise: the reader deletes an acknowledged session
from its card, never re-sends an acknowledged day, and prunes old months.
So nothing here answers "accepted" for data it did not store. A record Tome
refuses stays at the head of the reader's queue and blocks everything behind
it, so validation follows the firmware's own record check, with one addition:
a session that ends more than a day in the future is refused (a reader with a
broken clock; the firmware itself holds such records back once its clock is
right again).

The reader sends a session more than once: as the sitting goes on, after a
lost reply, after an SD card is restored from a backup. Each upload is compared
with what the reader reported last time (``KOSyncSessionReceipt``), never with
the ``ReadingSession`` row, so a session the user trimmed or deleted in Tome is
not undone by a repeat.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.kosync import (
    KOSyncBookStats,
    KOSyncDailyStats,
    KOSyncDeviceStats,
    KOSyncDocumentMap,
    KOSyncSessionReceipt,
)
from backend.models.tome_sync import ReadingSession

MAX_ITEMS = 20                 # per request, every endpoint
MIN_SESSION_SECONDS = 10       # the firmware does not export shorter fragments
MAX_SESSION_SPAN = 7 * 86400
EPOCH_2000 = 946684800
# A reader whose clock runs ahead still has to be able to drain its queue; a
# session ending more than a day from now is a broken clock, not reading.
FUTURE_SLACK = 86400
MAX_SNAPSHOT_BYTES = 16 * 1024
UINT32 = 2**32 - 1
FALLBACK_DEVICE = "KOSync reader"

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DOCUMENT = re.compile(r"^[0-9a-fA-F]{32}$")


class Rejected(Exception):
    """The upload cannot be stored. Nothing was written."""

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _int(value: Any, what: str, lo: int = 0, hi: int = UINT32) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise Rejected(f"Invalid {what}")
    return value


def _device_id(body: dict[str, Any]) -> str:
    value = body.get("device_id")
    if not isinstance(value, str) or not _ID.match(value):
        raise Rejected("Invalid device_id")
    return value


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise Rejected(f"items must hold 1 to {MAX_ITEMS} records")
    if not all(isinstance(i, dict) for i in items):
        raise Rejected("Invalid items")
    return items


def _utc(ts: int) -> datetime:
    """Epoch seconds to the naive-UTC datetime the DB stores."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _device_row(db: Session, user_id: int, device_id: str) -> Optional[KOSyncDeviceStats]:
    return (
        db.query(KOSyncDeviceStats)
        .filter(KOSyncDeviceStats.user_id == user_id, KOSyncDeviceStats.device_id == device_id)
        .first()
    )


def valid_device_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


# ── lifetime snapshot + daily seconds ────────────────────────────────────────

def parse_day(entry: Any) -> tuple[str, int]:
    """One ``{"date", "seconds"}`` daily entry, validated: (``YYYY-MM-DD``,
    seconds). Raises ``Rejected``."""
    if not isinstance(entry, dict) or not isinstance(entry.get("date"), str):
        raise Rejected("Invalid daily entry")
    try:
        day = date.fromisoformat(entry["date"])
    except ValueError:
        raise Rejected("Invalid daily date") from None
    if not 2000 <= day.year <= 2099:
        raise Rejected("Invalid daily date")
    return day.isoformat(), _int(entry.get("seconds"), "daily seconds")


def snapshot_json(body: dict[str, Any]) -> str:
    """The stored form of a device's lifetime snapshot. Raises ``Rejected``
    when it is too large."""
    snapshot = json.dumps({k: v for k, v in body.items() if k != "daily"}, separators=(",", ":"))
    if len(snapshot) > MAX_SNAPSHOT_BYTES:
        raise Rejected("Snapshot too large")
    return snapshot


def store_global(db: Session, user_id: int, body: dict[str, Any]) -> int:
    """Store a device's lifetime snapshot and any daily seconds sent with it.

    Returns the number of daily rows stored (the reader checks it).
    """
    device_id = _device_id(body)
    name = body.get("device")
    name = name.strip()[:255] if isinstance(name, str) and name.strip() else None

    daily: dict[str, int] = {}
    raw_daily = body.get("daily")
    if raw_daily is not None:
        if not isinstance(raw_daily, list) or len(raw_daily) > MAX_ITEMS:
            raise Rejected(f"daily must hold at most {MAX_ITEMS} days")
        for entry in raw_daily:
            key, seconds = parse_day(entry)
            daily[key] = max(daily.get(key, 0), seconds)

    snapshot = snapshot_json(body)

    now = datetime.utcnow()
    row = _device_row(db, user_id, device_id)
    if row is None:
        row = KOSyncDeviceStats(user_id=user_id, device_id=device_id)
        db.add(row)
    row.device = name or row.device
    row.snapshot = snapshot
    row.updated_at = now

    merge_daily(db, user_id, device_id, daily, now)
    db.commit()
    return len(daily)


def merge_daily(
    db: Session, user_id: int, device_id: str, daily: dict[str, int], now: datetime,
) -> dict[str, str]:
    """Store a device's seconds per day (``YYYY-MM-DD`` -> seconds, already
    validated), keeping the larger value for a day Tome already has. Returns
    per day what happened: ``created``, ``raised`` or ``unchanged``. Does not
    commit."""
    outcome: dict[str, str] = {}
    if not daily:
        return outcome
    existing = {
        r.day: r
        for r in db.query(KOSyncDailyStats).filter(
            KOSyncDailyStats.user_id == user_id,
            KOSyncDailyStats.device_id == device_id,
            KOSyncDailyStats.day.in_(list(daily)),
        )
    }
    for day_key, seconds in daily.items():
        day_row = existing.get(day_key)
        if day_row is None:
            db.add(KOSyncDailyStats(
                user_id=user_id, device_id=device_id, day=day_key,
                seconds=seconds, updated_at=now,
            ))
            outcome[day_key] = "created"
        elif seconds > day_row.seconds:
            day_row.seconds = seconds
            day_row.updated_at = now
            outcome[day_key] = "raised"
        else:
            outcome[day_key] = "unchanged"
    return outcome


# ── sessions ─────────────────────────────────────────────────────────────────

@dataclass
class _SessionItem:
    session_id: str
    document: str
    started_at: datetime
    ended_at: datetime
    seconds: int


def parse_session(item: dict[str, Any], now_ts: int) -> _SessionItem:
    """One session as the reader sends it, validated. Raises ``Rejected``."""
    session_id = item.get("session_id")
    if not isinstance(session_id, str) or not _ID.match(session_id):
        raise Rejected("Invalid session_id")
    document = item.get("document")
    if not isinstance(document, str) or not _DOCUMENT.match(document):
        raise Rejected("Invalid document")
    started = _int(item.get("started_at"), "started_at", lo=EPOCH_2000, hi=2**40)
    ended = _int(item.get("ended_at"), "ended_at", lo=started, hi=2**40)
    span = ended - started
    seconds = _int(item.get("seconds"), "seconds", lo=MIN_SESSION_SECONDS)
    if span > MAX_SESSION_SPAN or seconds > span:
        raise Rejected("Session duration does not fit its time span")
    if ended > now_ts + FUTURE_SLACK:
        raise Rejected("Session ends in the future")
    return _SessionItem(session_id, document.lower(), _utc(started), _utc(ended), seconds)


def store_sessions(
    db: Session,
    user_id: int,
    body: dict[str, Any],
    resolve_book: Callable[[str], Optional[int]],
    now_ts: int,
) -> list[str]:
    """Store measured reading sessions. Returns the acknowledged session ids.

    The whole batch is validated before anything is written; see
    ``stage_sessions`` for what happens to each session.
    """
    device_id = _device_id(body)
    parsed = [parse_session(item, now_ts) for item in _items(body)]
    stage_sessions(db, user_id, device_id, parsed, resolve_book)
    db.commit()
    return [item.session_id for item in parsed]


def stage_sessions(
    db: Session,
    user_id: int,
    device_id: str,
    parsed: list[_SessionItem],
    resolve_book: Callable[[str], Optional[int]],
    fallback_name: Optional[str] = None,
    known_receipts: Optional[dict[str, Optional[KOSyncSessionReceipt]]] = None,
) -> list[tuple[str, int]]:
    """Write parsed sessions of one device. Does not commit.

    A session seen for the first time becomes a ``ReadingSession`` plus a
    receipt. A session seen before is compared with its receipt: only reading
    time beyond what the reader reported last time is added, so a repeat adds
    nothing, a trimmed session keeps its trim, and a session the user deleted
    stays deleted. Raises ``Rejected`` (409), before writing anything, when a
    session id already names a different session.

    ``resolve_book`` maps a document hash to a book id, or None. A session for
    a book Tome cannot identify is still stored, without a book: see
    ``attach_sessions``. The sessions are named after the device's stored
    snapshot, else ``fallback_name``, else ``FALLBACK_DEVICE``.

    ``known_receipts`` (session id -> receipt, None for none) saves a query
    per session for a caller that loaded them all at once; ids it does not
    hold are looked up as usual.

    Returns per item what happened and how many seconds of reading it added:
    ``created``, ``extended`` (the session grew), ``unchanged``, or
    ``deleted_in_tome`` (it grew, but the user deleted it in Tome, so it
    stays deleted).
    """
    device_row = _device_row(db, user_id, device_id)
    device_name = (device_row.device if device_row else None) or fallback_name or FALLBACK_DEVICE

    # Pass 1, read-only: every item must fit the session its id already names,
    # in the database or earlier in this batch. Nothing is written until the
    # whole batch has passed.
    receipts: dict[str, Optional[KOSyncSessionReceipt]] = {}
    identity: dict[str, tuple[str, datetime]] = {}
    for item in parsed:
        if item.session_id not in receipts and known_receipts is not None and item.session_id in known_receipts:
            receipt = known_receipts[item.session_id]
            receipts[item.session_id] = receipt
            identity[item.session_id] = (
                (receipt.document, receipt.started_at) if receipt is not None
                else (item.document, item.started_at)
            )
        if item.session_id not in receipts:
            receipt = (
                db.query(KOSyncSessionReceipt)
                .filter(KOSyncSessionReceipt.user_id == user_id,
                        KOSyncSessionReceipt.session_uuid == item.session_id)
                .first()
            )
            receipts[item.session_id] = receipt
            identity[item.session_id] = (
                (receipt.document, receipt.started_at) if receipt is not None
                else (item.document, item.started_at)
            )
        if identity[item.session_id] != (item.document, item.started_at):
            raise Rejected("Session id was reused for a different session", status=409)

    # Linking a document can commit, so resolve books before the first write.
    books: dict[str, Optional[int]] = {}
    for item in parsed:
        if receipts[item.session_id] is None and item.document not in books:
            books[item.document] = resolve_book(item.document)

    # Pass 2. The app's sessions do not autoflush, so a receipt added for one
    # item is only visible to the next through ``receipts``.
    now = datetime.utcnow()
    outcomes: list[tuple[str, int]] = []
    for item in parsed:
        receipt = receipts[item.session_id]
        if receipt is None:
            row = ReadingSession(
                user_id=user_id,
                book_id=books[item.document],
                started_at=item.started_at,
                ended_at=item.ended_at,
                duration_seconds=item.seconds,
                device=device_name,
            )
            receipt = KOSyncSessionReceipt(
                user_id=user_id, device_id=device_id, session_uuid=item.session_id,
                document=item.document, started_at=item.started_at, ended_at=item.ended_at,
                seconds=item.seconds, reading_session=row, updated_at=now,
            )
            db.add(row)
            db.add(receipt)
            receipts[item.session_id] = receipt
            outcomes.append(("created", item.seconds))
            continue
        grown = item.seconds - receipt.seconds
        if grown <= 0 and item.ended_at <= receipt.ended_at:
            outcomes.append(("unchanged", 0))
            continue   # a repeat, or an older checkpoint arriving late
        row = receipt.reading_session
        if row is not None:   # None: the user deleted the session in Tome
            if grown > 0:
                row.duration_seconds = (row.duration_seconds or 0) + grown
            if row.ended_at is None or item.ended_at > row.ended_at:
                row.ended_at = item.ended_at
        receipt.seconds = max(receipt.seconds, item.seconds)
        receipt.ended_at = max(receipt.ended_at, item.ended_at)
        receipt.updated_at = now
        if row is None:
            outcomes.append(("deleted_in_tome", 0))
        else:
            outcomes.append(("extended", max(grown, 0)))
    return outcomes


def has_sessions_for(db: Session, user_id: int, document: str) -> bool:
    """Has this user uploaded any session for ``document``?"""
    return db.query(KOSyncSessionReceipt.id).filter(
        KOSyncSessionReceipt.user_id == user_id,
        KOSyncSessionReceipt.document == document.lower(),
    ).first() is not None


def attach_sessions(
    db: Session, user_id: int, document: str, book_id: int, relink: bool = False,
) -> int:
    """Point a user's uploaded sessions for ``document`` at ``book_id``.

    By default only sessions that have no book yet (they arrived before the
    document was linked). ``relink`` moves all of them, for when the user
    corrects a link by hand. Does not commit.
    """
    session_ids = (
        db.query(KOSyncSessionReceipt.reading_session_id)
        .filter(
            KOSyncSessionReceipt.user_id == user_id,
            KOSyncSessionReceipt.document == document.lower(),
            KOSyncSessionReceipt.reading_session_id.isnot(None),
        )
    )
    query = db.query(ReadingSession).filter(
        ReadingSession.user_id == user_id,
        ReadingSession.id.in_(session_ids),
    )
    if not relink:
        query = query.filter(ReadingSession.book_id.is_(None))
    return query.update({ReadingSession.book_id: book_id}, synchronize_session=False)


def unlinked_session_titles(db: Session, user_id: int, session_ids: list[int]) -> dict[int, Optional[str]]:
    """Which of these book-less sessions were uploaded by a reader (as opposed
    to belonging to a book that has since been deleted), each with a label
    from the reader's own description of the file when it sent one."""
    if not session_ids:
        return {}
    from backend.services.kosync_books import titles_for

    documents = dict(
        db.query(KOSyncSessionReceipt.reading_session_id, KOSyncSessionReceipt.document)
        .filter(KOSyncSessionReceipt.reading_session_id.in_(session_ids))
    )
    titles = titles_for(db, user_id, sorted(set(documents.values())))
    return {
        sid: (f"{titles[doc]} (not linked yet)" if doc in titles else None)
        for sid, doc in documents.items()
    }


# ── per-book snapshots ───────────────────────────────────────────────────────

def check_books(body: dict[str, Any]) -> int:
    """Validate a per-book snapshot batch and return how many it holds.

    Only the device id and each item's document are required. Counters that
    are missing or malformed are stored as unknown rather than refused: a
    refused batch would block the reader's queue, and these totals are sent
    again whenever they change.
    """
    _device_id(body)
    items = _items(body)
    for item in items:
        document = item.get("document")
        if not isinstance(document, str) or not _DOCUMENT.match(document):
            raise Rejected("Invalid document")
    return len(items)


def _counter(value: Any) -> Optional[int]:
    """A uint32 counter from a snapshot, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UINT32:
        return None
    return value


def book_documents(body: dict[str, Any]) -> list[str]:
    """The distinct documents of a batch that passed ``check_books``, lower-case."""
    return sorted({item["document"].lower() for item in body["items"]})


def store_books(db: Session, user_id: int, body: dict[str, Any]) -> int:
    """Store a batch of per-book snapshots. Returns the number of items.

    Each item replaces what this device last sent for its document, also when
    the new numbers are lower (the reader's stats were reset or its card was
    restored): the snapshot is the reader's current truth. Rows hang off the
    document; see ``KOSyncBookStats``.
    """
    count = check_books(body)
    latest: dict[str, dict[str, Any]] = {}
    for item in body["items"]:   # a document sent twice in one batch: the last one counts
        latest[item["document"].lower()] = item
    stage_books(db, user_id, body["device_id"], latest)
    db.commit()
    return count


def stage_books(
    db: Session, user_id: int, device_id: str, items: dict[str, dict[str, Any]],
    sent_at: Optional[dict[str, datetime]] = None,
) -> dict[str, str]:
    """Write per-book snapshots of one device (document, lower-case -> item,
    already checked). Does not commit.

    Without ``sent_at`` every item replaces the stored row (the live sync: the
    newest upload is the reader's truth). With ``sent_at`` (document -> when
    the reader sent that snapshot) a stored row is only replaced by an item
    sent later than the row was stored, so older history never overwrites a
    newer snapshot; the row then keeps the item's own time.

    Returns per document: ``created``, ``replaced``, ``unchanged`` (same
    numbers) or ``kept_newer`` (Tome's row is newer and stays).
    """
    existing = {
        row.document: row
        for row in db.query(KOSyncBookStats).filter(
            KOSyncBookStats.user_id == user_id,
            KOSyncBookStats.device_id == device_id,
            KOSyncBookStats.document.in_(list(items)),
        )
    }
    now = datetime.utcnow()
    outcome: dict[str, str] = {}
    for document, item in items.items():
        snapshot = json.dumps(item, separators=(",", ":"))
        if len(snapshot) > MAX_SNAPSHOT_BYTES:
            # Keep what Tome uses; the rest of an oversized item is not kept.
            snapshot = json.dumps({k: item.get(k) for k in ("document", "pages", "seconds")},
                                  separators=(",", ":"))
        pages, seconds = _counter(item.get("pages")), _counter(item.get("seconds"))
        when = sent_at.get(document) if sent_at is not None else None
        row = existing.get(document)
        if row is None:
            row = KOSyncBookStats(user_id=user_id, device_id=device_id, document=document)
            db.add(row)
            outcome[document] = "created"
        elif sent_at is not None and (when is None or when <= row.updated_at):
            outcome[document] = (
                "unchanged" if (row.pages, row.seconds) == (pages, seconds) else "kept_newer")
            continue
        else:
            outcome[document] = (
                "unchanged" if (row.pages, row.seconds, row.snapshot) == (pages, seconds, snapshot)
                else "replaced")
        row.pages = pages
        row.seconds = seconds
        row.snapshot = snapshot
        row.updated_at = when or now
    return outcome


def has_snapshot_for(db: Session, user_id: int, document: str) -> bool:
    """Has this user's reader uploaded a per-book snapshot for ``document``?"""
    return db.query(KOSyncBookStats.id).filter(
        KOSyncBookStats.user_id == user_id,
        KOSyncBookStats.document == document.lower(),
    ).first() is not None


@dataclass
class ReaderPages:
    """Pages a user's readers counted on one book, from their latest snapshots.

    ``pages`` is every page the snapshots report. ``paced_pages`` and
    ``paced_seconds`` are the part that comes with the reader's own reading
    time, the only pages that may go into a pace, and only over that time.
    """
    pages: int = 0
    paced_pages: int = 0
    paced_seconds: int = 0


def reader_pages(db: Session, user_id: int, book_ids: Optional[list[int]] = None) -> dict[int, ReaderPages]:
    """Per book, the pages in this user's latest per-book snapshots.

    Only the user's own snapshots, through the user's own document links (a
    document is linked to one book per user), summed over devices and over
    every document linked to the book. The caller decides whether the user
    may see the book. A snapshot whose document is not linked counts nowhere.
    """
    query = (
        db.query(KOSyncBookStats.id, KOSyncDocumentMap.book_id,
                 KOSyncBookStats.pages, KOSyncBookStats.seconds)
        .join(KOSyncDocumentMap, (KOSyncDocumentMap.tome_user_id == KOSyncBookStats.user_id)
              & (func.lower(KOSyncDocumentMap.document) == KOSyncBookStats.document))
        .filter(KOSyncBookStats.user_id == user_id, KOSyncBookStats.pages > 0)
    )
    if book_ids is not None:
        if not book_ids:
            return {}
        query = query.filter(KOSyncDocumentMap.book_id.in_(book_ids))
    out: dict[int, ReaderPages] = {}
    seen: set[int] = set()
    for row_id, book_id, pages, seconds in query.all():
        if row_id in seen:   # two links differing only in case
            continue
        seen.add(row_id)
        entry = out.setdefault(book_id, ReaderPages())
        entry.pages += pages
        if seconds and seconds > 0:
            entry.paced_pages += pages
            entry.paced_seconds += seconds
    return out


# ── summary ──────────────────────────────────────────────────────────────────

def summary(db: Session, user_id: int) -> dict[str, Any]:
    """Totals across the user's devices, summed from the stored snapshots.

    The reader asks for this to find out whether the server speaks the stats
    API at all; it only needs a JSON object back.
    """
    totals = {"sessions": 0, "seconds": 0, "pages": 0, "completed": 0}
    devices: list[dict[str, Any]] = []
    for row in (
        db.query(KOSyncDeviceStats)
        .filter(KOSyncDeviceStats.user_id == user_id)
        .order_by(KOSyncDeviceStats.device_id)
    ):
        try:
            snap = json.loads(row.snapshot or "{}")
        except ValueError:
            snap = {}
        for key in totals:
            value = snap.get(key) if isinstance(snap, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                totals[key] += value
        devices.append({
            "device_id": row.device_id,
            "device": row.device,
            "updated_at": int(row.updated_at.replace(tzinfo=timezone.utc).timestamp()),
        })
    return {**totals, "devices": devices}
