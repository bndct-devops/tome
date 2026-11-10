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
  about reading done on such a reader. The counter only grows, so the growth
  between two live snapshots is the pages turned in between, and it is
  attributed to the reader's sessions of that time (see "page attribution"
  below): those sessions then carry pages like any other, on every dated
  tile. What the counter holds beyond that (everything before Tome saw a
  first snapshot, growth no session has taken yet) still counts for the
  book's all-time pages and pace only (``reader_pages``).

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
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from backend.models.kosync import (
    KOSyncBookStats,
    KOSyncDailyStats,
    KOSyncDeviceStats,
    KOSyncDocumentMap,
    KOSyncSessionReceipt,
    ReadingHistory,
)
from backend.models.tome_sync import ReadingSession

log = logging.getLogger(__name__)

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
    imported: bool = False,
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

    A new receipt records when Tome received the session (``received_at``,
    the server's clock): that decides which page counter growth it can take.
    ``imported`` sessions come from history, not from the reader just now;
    they count as received when they ended (never later than now), so a live
    snapshot after the import's own never hands them its growth.

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
                received_at=min(item.ended_at, now) if imported else now,
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
    # Growth a snapshot left pending is not given out here: more sessions of
    # its window may still be on the reader's card (see "page attribution").
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


def attach_history(
    db: Session, user_id: int, document: str, book_id: int, relink: bool = False,
) -> int:
    """Point a user's logged progress pushes (``reading_history``) for
    ``document`` at ``book_id``, like ``attach_sessions`` does for sessions.

    Pushes made before the document had a book were logged without one; the
    book page's progress line reads them by book. ``relink`` moves all of the
    document's rows, for a link corrected by hand. Does not commit.
    """
    query = db.query(ReadingHistory).filter(
        ReadingHistory.user_id == user_id,
        func.lower(ReadingHistory.document) == document.lower(),
    )
    if relink:
        query = query.filter(
            (ReadingHistory.book_id.is_(None)) | (ReadingHistory.book_id != book_id)
        )
    else:
        query = query.filter(ReadingHistory.book_id.is_(None))
    return query.update({ReadingHistory.book_id: book_id}, synchronize_session=False)


def attach_all_history(db: Session, user_id: int) -> int:
    """Give every unlinked progress push of a user the book its document is
    now linked to, in one statement. For a caller that links many documents
    at once (the CrossPoint Sync import): one ``attach_history`` per
    document would look through the user's unlinked rows once per document.
    Rows whose document has no link stay unlinked. Flushes first so links
    added in this transaction count. Does not commit."""
    from sqlalchemy import select, update

    db.flush()
    linked = (
        select(KOSyncDocumentMap.book_id)
        .where(
            KOSyncDocumentMap.tome_user_id == user_id,
            func.lower(KOSyncDocumentMap.document) == func.lower(ReadingHistory.document),
        )
        .limit(1)
        .scalar_subquery()
    )
    result = db.execute(
        update(ReadingHistory)
        .where(
            ReadingHistory.user_id == user_id,
            ReadingHistory.book_id.is_(None),
            linked.isnot(None),
        )
        .values(book_id=linked)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


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
            # A first snapshot is the base later growth is measured from.
            row = KOSyncBookStats(user_id=user_id, device_id=device_id, document=document,
                                  counter_epoch=0, pages_attributed=0, pages_pending=0)
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
            if sent_at is None:
                _advance(db, row, pages, now)
            else:
                # History has no "previous" Tome saw: the imported counter is a
                # new base. Only a lower one changes the bookkeeping (a reset).
                _rebase(row, pages)
        row.pages = pages
        row.seconds = seconds
        row.snapshot = snapshot
        row.updated_at = when or now
    return outcome


# ── page attribution ─────────────────────────────────────────────────────────
#
# The reader's per-book counter (``pages``: forward page turns after a short
# dwell) only grows, and the reader sends a snapshot whenever it changed. So
# the growth between two snapshots of one (user, device, document) is exactly
# the pages turned between them. It goes to that device's sessions of that
# document that Tome received after the earlier snapshot and no later than
# the newer one (``KOSyncSessionReceipt.received_at``, both times the
# server's clock), split by their reading seconds (largest remainders, so the
# pieces add up to the growth), and is marked as attributed
# (``ReadingSession.pages_attributed``).
#
# Arrival, not the reader's clock, decides: the reader sends its queued
# sessions before its book snapshots in every flow (ReadingSyncUpload::
# globalStats, then the per-book stats), so a session in the database when a
# snapshot arrives was read before it. A reader clock running ahead or behind
# therefore cannot move a sitting into the wrong window.
#
# Weights are the sessions' ``duration_seconds``, the same seconds a session
# brings into the pace, so a trim before or after attribution keeps the
# book's pace equal to the reader's.
#
# A sitting that goes on past a sync (the firmware re-sends it every few
# minutes with a later end) took pages for the time it had then
# (``attributed_seconds``); in the next window it takes a share for the time
# it gained since, added to its pages.
#
# Every window is closed: when the growth is 0, or unknown (a reset, a counter
# Tome could not read), its sessions are marked with what they got (0 pages
# then), so they never take a later window's growth. A window next to an
# unreadable counter is never attributed; its pages stay with the counter's
# share, so all-time totals are unchanged.
#
# A snapshot that finds no session at all means the session upload failed
# partway and that window's sessions are still on the card; they come on a
# later sync. The growth then waits on the row (``pages_pending``) and is
# given out at the next snapshot: first over the late sessions that clearly
# belong to the old window (received since, and ended by the reader's clock no
# later than that snapshot plus ``OLD_WINDOW_SLACK``), then the new growth
# over the rest. If no late session is there, the waiting pages join the new
# growth. Consequence: after a failed session upload those pages show up one
# sync later. (A snapshot that finds only some of its window's sessions, the
# rest still on the card, gives them all of it; Tome cannot tell that apart.)
#
# Never touched: a session that counted its own pages, one the user deleted.
# A trimmed session keeps its pages.
#
# The book's all-time pages add each row's ``pages`` minus the attributed
# pages of its sessions that still exist and count in Tome's totals
# (``reader_pages``), so they equal the counter whether or not anything was
# attributed, and a deleted or superseded session's pages fall back to the
# counter's share.
#
# A reset (a lower counter: "Delete book stats", a restored card) starts a
# new epoch: nothing is attributed from it, and the pages sessions already
# hold stay theirs, because dated history is worth more than matching the
# reader after a reset. From then on the book's all-time pages are those
# session pages plus the new counter, no longer equal to the counter alone
# (with a card restored to an older count, the stretch between the two
# counts is then counted twice).

OLD_WINDOW_SLACK = timedelta(minutes=5)


@dataclass
class _Candidate:
    receipt: KOSyncSessionReceipt
    session: ReadingSession
    weight: int


def _candidates(
    db: Session, row: KOSyncBookStats, after: datetime, until: datetime,
    ended_by: Optional[datetime] = None,
) -> list[_Candidate]:
    """The row's sessions that can take growth received in (``after``,
    ``until``]: sessions without pages received then, and (unless
    ``ended_by`` limits it to late sessions of an old window ending by then)
    sessions of this epoch that took pages before and have grown since."""
    base = (
        db.query(KOSyncSessionReceipt, ReadingSession)
        .join(ReadingSession, ReadingSession.id == KOSyncSessionReceipt.reading_session_id)
        .filter(
            KOSyncSessionReceipt.user_id == row.user_id,
            KOSyncSessionReceipt.device_id == row.device_id,
            KOSyncSessionReceipt.document == row.document,
        )
    )
    fresh = base.filter(
        KOSyncSessionReceipt.received_at > after,
        KOSyncSessionReceipt.received_at <= until,
        ReadingSession.pages_turned.is_(None),
        ReadingSession.pages_attributed.is_(False),
    )
    if ended_by is not None:
        fresh = fresh.filter(KOSyncSessionReceipt.ended_at <= ended_by)
    out = [_Candidate(r, s, max(s.duration_seconds or 0, 0))
           for r, s in fresh.order_by(KOSyncSessionReceipt.received_at, KOSyncSessionReceipt.id)]
    if ended_by is None:
        grown = base.filter(
            ReadingSession.pages_attributed.is_(True),
            KOSyncSessionReceipt.pages_epoch == (row.counter_epoch or 0),
            KOSyncSessionReceipt.attributed_seconds.isnot(None),
            ReadingSession.duration_seconds > KOSyncSessionReceipt.attributed_seconds,
        ).order_by(KOSyncSessionReceipt.received_at, KOSyncSessionReceipt.id)
        out += [_Candidate(r, s, s.duration_seconds - r.attributed_seconds) for r, s in grown]
    return out


def _give(row: KOSyncBookStats, candidates: list[_Candidate], amount: int) -> None:
    """Split ``amount`` (0 closes the window) over ``candidates``."""
    for c, share in zip(candidates, split(amount, [c.weight for c in candidates])):
        c.session.pages_turned = (c.session.pages_turned or 0) + share
        c.session.pages_attributed = True
        c.receipt.pages_epoch = row.counter_epoch or 0
        c.receipt.attributed_seconds = c.session.duration_seconds or 0
    row.pages_attributed = (row.pages_attributed or 0) + amount


def _advance(db: Session, row: KOSyncBookStats, pages: Optional[int], now: datetime) -> None:
    """A live snapshot replaces ``row`` (not yet updated): attribute the
    counter's growth since the stored snapshot. Does not commit."""
    old, prev = row.pages, row.updated_at
    if old is None or pages is None:
        # No counter to measure from or to: the window's growth is unknown.
        # Close it (its sessions keep no pages from it) and start a new base.
        _give(row, _candidates(db, row, prev, now), 0)
        row.pages_pending, row.pending_since = 0, None
        return
    if pages < old:
        log.info(
            "KOSync reader page counter went down (user %s, device %s, document %s: %s -> %s); "
            "treating it as a reset, new base, nothing attributed",
            row.user_id, row.device_id, row.document, old, pages)
        _give(row, _candidates(db, row, prev, now), 0)
        _reset(row)
        return
    if row.pages_pending > 0:
        late = _candidates(db, row, prev, now, ended_by=prev + OLD_WINDOW_SLACK)
        if late:
            _give(row, late, row.pages_pending)
            row.pages_pending, row.pending_since = 0, None
            db.flush()   # the app's sessions do not autoflush; the next query must see this
    amount = pages - old + row.pages_pending
    candidates = _candidates(db, row, prev, now)   # without those just given pages
    if not candidates:
        if amount > 0:
            row.pages_pending = amount
            row.pending_since = row.pending_since or prev
        return
    _give(row, candidates, amount)
    row.pages_pending, row.pending_since = 0, None


def _rebase(row: KOSyncBookStats, pages: Optional[int]) -> None:
    """An imported snapshot replaces ``row``: no attribution, but a lower
    counter is a reset like a live one."""
    if row.pages is not None and pages is not None and pages < row.pages:
        log.info(
            "Imported page counter is lower than Tome's (user %s, device %s, document %s: %s -> %s); "
            "treating it as a reset",
            row.user_id, row.device_id, row.document, row.pages, pages)
        _reset(row)


def _reset(row: KOSyncBookStats) -> None:
    row.counter_epoch = (row.counter_epoch or 0) + 1
    row.pages_attributed = 0
    row.pages_pending, row.pending_since = 0, None


def split(amount: int, weights: list[int]) -> list[int]:
    """``amount`` split in proportion to ``weights``, in whole numbers that add
    up to it exactly: everyone gets the floor of their share, and what is left
    goes one each to the largest remainders (earlier first on a tie)."""
    weights = [max(int(w or 0), 0) for w in weights]
    total = sum(weights)
    if total == 0:   # cannot happen for real sessions (10 s minimum): split evenly
        weights, total = [1] * len(weights), len(weights)
    shares, remainders = [], []
    for i, w in enumerate(weights):
        q, r = divmod(amount * w, total)
        shares.append(q)
        remainders.append((-r, i))
    for _, i in sorted(remainders)[: amount - sum(shares)]:
        shares[i] += 1
    return shares


def has_snapshot_for(db: Session, user_id: int, document: str) -> bool:
    """Has this user's reader uploaded a per-book snapshot for ``document``?"""
    return db.query(KOSyncBookStats.id).filter(
        KOSyncBookStats.user_id == user_id,
        KOSyncBookStats.document == document.lower(),
    ).first() is not None


@dataclass
class ReaderPages:
    """Pages a user's readers counted on one book that no session carries.

    ``pages`` is what the latest snapshots report minus the pages attributed
    from them to sessions that still exist (those count as session pages).
    ``paced_pages`` and ``paced_seconds`` are the part that comes with the
    reader's own reading time, the only pages that may go into a pace, and
    only over that time: the reader's seconds minus those of the sessions that
    took pages (they are paced as sessions).
    """
    pages: int = 0
    paced_pages: int = 0
    paced_seconds: int = 0
    counter: int = 0   # the snapshots' pages as sent, attributed or not


def _attributed_by_row(db: Session, user_id: int) -> dict[tuple[str, str, int], tuple[int, int]]:
    """(device_id, document, epoch) -> (pages, paced seconds) of the sessions
    that still exist, count in Tome's totals and were given pages from that
    counter. The seconds are what those sessions bring into a pace
    (``duration_seconds`` where they have pages)."""
    from backend.services.reconciled_reading import counted_clause
    rows = (
        db.query(KOSyncSessionReceipt.device_id, KOSyncSessionReceipt.document,
                 KOSyncSessionReceipt.pages_epoch,
                 func.coalesce(func.sum(ReadingSession.pages_turned), 0),
                 func.coalesce(func.sum(case((ReadingSession.pages_turned > 0, ReadingSession.duration_seconds),
                                             else_=0)), 0))
        .join(ReadingSession, ReadingSession.id == KOSyncSessionReceipt.reading_session_id)
        .filter(KOSyncSessionReceipt.user_id == user_id,
                KOSyncSessionReceipt.pages_epoch.isnot(None),
                ReadingSession.pages_attributed.is_(True),
                # A session imported KOReader history supersedes is out of
                # Tome's totals, so its pages count with the counter again.
                counted_clause())
        .group_by(KOSyncSessionReceipt.device_id, KOSyncSessionReceipt.document,
                  KOSyncSessionReceipt.pages_epoch)
        .all()
    )
    return {(d, doc, epoch): (int(p or 0), int(s or 0)) for d, doc, epoch, p, s in rows}


def reader_pages(db: Session, user_id: int, book_ids: Optional[list[int]] = None) -> dict[int, ReaderPages]:
    """Per book, the pages in this user's latest per-book snapshots that no
    session carries.

    Only the user's own snapshots, through the user's own document links (a
    document is linked to one book per user), summed over devices and over
    every document linked to the book. The caller decides whether the user
    may see the book. A snapshot whose document is not linked counts nowhere.

    Adding this to the book's session pages gives the reader's counter
    exactly: a session's attributed pages are subtracted here only while the
    session exists, so deleting one moves its pages back to the snapshot.
    """
    query = (
        db.query(KOSyncBookStats.id, KOSyncDocumentMap.book_id, KOSyncBookStats.device_id,
                 KOSyncBookStats.document, KOSyncBookStats.counter_epoch,
                 KOSyncBookStats.pages, KOSyncBookStats.seconds)
        .join(KOSyncDocumentMap, (KOSyncDocumentMap.tome_user_id == KOSyncBookStats.user_id)
              & (func.lower(KOSyncDocumentMap.document) == KOSyncBookStats.document))
        .filter(KOSyncBookStats.user_id == user_id, KOSyncBookStats.pages > 0)
    )
    if book_ids is not None:
        if not book_ids:
            return {}
        query = query.filter(KOSyncDocumentMap.book_id.in_(book_ids))
    rows = query.all()
    if not rows:
        return {}
    given = _attributed_by_row(db, user_id)
    out: dict[int, ReaderPages] = {}
    seen: set[int] = set()
    for row_id, book_id, device_id, document, epoch, pages, seconds in rows:
        if row_id in seen:   # two links differing only in case
            continue
        seen.add(row_id)
        given_pages, given_seconds = given.get((device_id, document, epoch or 0), (0, 0))
        rest_pages = max(pages - given_pages, 0)
        entry = out.setdefault(book_id, ReaderPages())
        entry.counter += pages
        entry.pages += rest_pages
        rest_seconds = (seconds or 0) - given_seconds
        if seconds and seconds > 0 and rest_seconds > 0:
            entry.paced_pages += rest_pages
            entry.paced_seconds += rest_seconds
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
