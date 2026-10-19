"""Push a user's daily reading totals to Nightlamp (contract schema v1, §2).

Nothing here runs unless the feature is on (``settings.platform_active``) and the user linked
their account in Settings (backend/api/platform.py).

Days are the user's LOCAL days in their IANA zone with exact midnights: a
session that crosses midnight is split by time. Tome's own 04:00 rollover
(reading_day.py) is deliberately not used here.

What leaves the server per day: minutes read, minutes per book type, sittings,
the longest sitting and the number of books finished. Titles, authors and
ISBNs plus a small cover (``finished`` / ``current``) only when the user
turned on "share titles and covers". Nightlamp looks covers up in public
catalogues by ISBN first and uses the one from Tome only when it finds none;
each cover goes once per book (again after "Resync everything"). Book and user ids never leave: both are sha256(instance_secret + id).

Triggers: an ORM hook marks a user dirty when a reading session is written or
a book is marked read/reading; ``platform_loop`` pushes dirty users two
minutes after their last change and sweeps every linked user hourly.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import secrets
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import decrypt_secret, encrypt_secret
from backend.models.book import Book
from backend.models.instance_setting import InstanceSetting
from backend.models.platform import PlatformCoverSent, PlatformLink
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
KEY_INSTANCE_ID = "platform.instance_id"
KEY_INSTANCE_SECRET = "platform.instance_secret"

SITTING_GAP_SECONDS = 15 * 60
DEBOUNCE_SECONDS = 120
TICK_SECONDS = 30
SWEEP_SECONDS = 3600
HTTP_TIMEOUT = 30.0
MAX_BODY = 8 * 1024 * 1024
BODY_BUDGET = 7 * 1024 * 1024  # split before Nightlamp's 8 MB limit
CHUNK_DAYS = 200
COVER_WIDTH = 160
COVER_QUALITY = 80
MAX_COVER = 64 * 1024


def enabled() -> bool:
    return settings.platform_active


def base_url() -> str:
    return (settings.platform_url or "").rstrip("/")


# ── instance identity ────────────────────────────────────────────────────────

def _get_setting(db: Session, key: str) -> str | None:
    row = db.get(InstanceSetting, key)
    return row.value if row else None


def instance_identity(db: Session) -> tuple[str, str]:
    """(instance id, instance secret), created on first use. The secret is
    stored encrypted with the server key, like the AI keys."""
    from backend.services.ai.settings import _set  # shared instance_settings writer

    iid = _get_setting(db, KEY_INSTANCE_ID)
    secret = decrypt_secret(_get_setting(db, KEY_INSTANCE_SECRET))
    if iid and secret:
        return iid, secret
    iid = secrets.token_urlsafe(16)          # 22 chars
    secret = secrets.token_urlsafe(32)       # 43 chars
    _set(db, KEY_INSTANCE_ID, iid)
    _set(db, KEY_INSTANCE_SECRET, encrypt_secret(secret))
    db.commit()
    return iid, secret


def _sha(secret: str, value: int | str) -> str:
    return hashlib.sha256((secret + str(value)).encode()).hexdigest()


def tome_user_id(secret: str, user_id: int) -> str:
    return _sha(secret, user_id)


def book_key(secret: str, book_id: int) -> str:
    return _sha(secret, book_id)


# ── aggregation ──────────────────────────────────────────────────────────────

def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass
class Segment:
    """Part of one session inside one local day (UTC bounds, reading seconds)."""
    day: date
    start: datetime
    end: datetime
    seconds: float
    book_id: int | None


def session_span(s: ReadingSession) -> tuple[datetime, datetime, float] | None:
    """(start, end, reading seconds) in aware UTC, or None for a session that
    has neither an end nor a duration."""
    if s.started_at is None:
        return None
    start = _utc(s.started_at)
    if s.ended_at is None:
        if not s.duration_seconds or s.duration_seconds <= 0:
            return None
        end = start + timedelta(seconds=s.duration_seconds)
    else:
        end = _utc(s.ended_at)
    seconds = float(s.duration_seconds) if s.duration_seconds is not None else (end - start).total_seconds()
    if seconds <= 0:
        return None
    if end < start:
        end = start
    return start, end, seconds


def split_by_midnight(start: datetime, end: datetime, seconds: float, tz: ZoneInfo,
                      book_id: int | None = None) -> list[Segment]:
    """Spread ``seconds`` over the local days the span [start, end] touches,
    proportionally by time. All arithmetic in UTC (aware datetimes with one
    shared tzinfo subtract as wall clock, which is wrong across DST)."""
    if end <= start:
        return [Segment(start.astimezone(tz).date(), start, end, seconds, book_id)]
    total = (end - start).total_seconds()
    out: list[Segment] = []
    cur = start
    while cur < end:
        local_day = cur.astimezone(tz).date()
        next_mid = datetime.combine(local_day + timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)
        seg_end = min(end, next_mid)
        part = (seg_end - cur).total_seconds()
        out.append(Segment(local_day, cur, seg_end, seconds * part / total, book_id))
        cur = seg_end
    return out


def merge_sittings(segments: Iterable[Segment], gap: int = SITTING_GAP_SECONDS) -> list[float]:
    """Reading seconds per sitting: segments closer than ``gap`` are one sitting."""
    out: list[list[Any]] = []  # [end, seconds]
    for seg in sorted(segments, key=lambda s: s.start):
        if out and (seg.start - out[-1][0]).total_seconds() < gap:
            out[-1][0] = max(out[-1][0], seg.end)
            out[-1][1] += seg.seconds
        else:
            out.append([seg.end, seg.seconds])
    return [s for _e, s in out]


def _local_date(dt: datetime, tz: ZoneInfo) -> date:
    return _utc(dt).astimezone(tz).date()


def _visible_book_ids(db: Session, user: User, ids: Iterable[int]) -> set[int]:
    ids = set(ids)
    if not ids:
        return set()
    from backend.core.permissions import book_visibility_filter
    rows = db.query(Book.id).filter(Book.id.in_(ids), Book.status == "active",
                                    book_visibility_filter(db, user)).all()
    return {r[0] for r in rows}


@dataclass
class Aggregate:
    days: dict[date, dict] = field(default_factory=dict)
    finished_by_day: dict[date, list[int]] = field(default_factory=dict)
    seconds_by_book: dict[int, float] = field(default_factory=dict)
    segments_by_book: dict[int, list[Segment]] = field(default_factory=dict)
    first_day: date | None = None


def aggregate(db: Session, user: User, tz: ZoneInfo) -> Aggregate:
    """Every local day the user read on, plus per-book totals."""
    agg = Aggregate()
    sessions = db.query(ReadingSession).filter(ReadingSession.user_id == user.id).all()
    book_ids = {s.book_id for s in sessions if s.book_id is not None}
    books = {b.id: b for b in db.query(Book).filter(Book.id.in_(book_ids)).all()} if book_ids else {}

    by_day: dict[date, list[Segment]] = {}
    for s in sessions:
        span = session_span(s)
        if span is None:
            continue
        for seg in split_by_midnight(*span, tz, s.book_id):
            by_day.setdefault(seg.day, []).append(seg)
            if s.book_id is not None:
                agg.seconds_by_book[s.book_id] = agg.seconds_by_book.get(s.book_id, 0.0) + seg.seconds
                agg.segments_by_book.setdefault(s.book_id, []).append(seg)

    for day, segs in by_day.items():
        by_type: dict[str, float] = {}
        for seg in segs:
            b = books.get(seg.book_id) if seg.book_id is not None else None
            slug = b.book_type.slug if b is not None and b.book_type is not None else "other"
            by_type[slug] = by_type.get(slug, 0.0) + seg.seconds
        sittings = merge_sittings(segs)
        agg.days[day] = {
            "minutes_read": round(sum(s.seconds for s in segs) / 60),
            "minutes_by_type": {k: round(v / 60) for k, v in sorted(by_type.items()) if round(v / 60) > 0},
            "sittings": len(sittings),
            "longest_sitting_minutes": round(max(sittings) / 60) if sittings else 0,
        }

    finished = (db.query(UserBookStatus)
                .filter(UserBookStatus.user_id == user.id, UserBookStatus.status == "read").all())
    visible = _visible_book_ids(db, user, (r.book_id for r in finished))
    for r in finished:
        if r.book_id not in visible:
            continue
        when = r.finished_at or r.updated_at
        if when is None:
            continue
        agg.finished_by_day.setdefault(_local_date(when, tz), []).append(r.book_id)

    all_days = list(agg.days) + list(agg.finished_by_day)
    agg.first_day = min(all_days) if all_days else None
    return agg


def _minutes(seconds: float) -> int:
    return round(seconds / 60)


# ── isbn ─────────────────────────────────────────────────────────────────────

def clean_isbn(raw: str | None) -> str | None:
    """The ISBN with hyphens and spaces stripped when it is 10 or 13 digits
    (an ISBN-10 may end in X), else None."""
    if not raw:
        return None
    v = raw.replace("-", "").replace(" ", "").strip().upper()
    if len(v) == 13 and v.isdigit():
        return v
    if len(v) == 10 and v[:9].isdigit() and (v[9].isdigit() or v[9] == "X"):
        return v
    return None


# ── covers ───────────────────────────────────────────────────────────────────

def cover_jpeg_b64(book: Book) -> str | None:
    """The book's cover as a 160 px wide JPEG (quality 80, lowered until it
    fits ``MAX_COVER``), base64, or None. cover_thumbs.py makes WebP for the
    web UI; Nightlamp takes JPEG only, so this resizes the cached original."""
    if not book.cover_path:
        return None
    src = settings.covers_dir / book.cover_path
    if not src.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(src) as img:
            img = img.convert("RGB")
            if img.width > COVER_WIDTH:
                img = img.resize((COVER_WIDTH, max(1, round(img.height * COVER_WIDTH / img.width))), Image.LANCZOS)
            for quality in (COVER_QUALITY, 65, 50, 35):
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)
                raw = buf.getvalue()
                if len(raw) <= MAX_COVER:
                    return base64.b64encode(raw).decode()
    except Exception as exc:  # noqa: BLE001 - a bad cover just goes without
        logger.warning("platform cover failed for book %s: %s", book.id, exc)
    return None


def _attach_cover(item: dict, book: Book, key: str, sent: set[str], cover_keys: set[str]) -> None:
    """Add ``cover_jpeg_b64`` unless this book's cover already went (or rides
    earlier in this push)."""
    if key in sent or key in cover_keys:
        return
    b64 = cover_jpeg_b64(book)
    if b64:
        item["cover_jpeg_b64"] = b64
        cover_keys.add(key)


# ── the envelope ─────────────────────────────────────────────────────────────

@dataclass
class Envelope:
    body: dict
    through: date
    cover_keys: set[str] = field(default_factory=set)


def _book_fields(book: Book, key: str) -> dict:
    return {"key": key, "title": book.title or "", "author": book.author, "series": book.series,
            "series_index": book.series_index, "isbn": clean_isbn(book.isbn)}


def _current(db: Session, user: User, agg: Aggregate, tz: ZoneInfo, secret: str,
             sent: set[str], cover_keys: set[str]) -> dict | None:
    rows = (db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.status == "reading").all())
    if not rows:
        return None
    visible = _visible_book_ids(db, user, (r.book_id for r in rows))
    rows = [r for r in rows if r.book_id in visible]
    if not rows:
        return None
    positions = {p.book_id: p.updated_at for p in db.query(TomeSyncPosition).filter(
        TomeSyncPosition.user_id == user.id, TomeSyncPosition.book_id.in_([r.book_id for r in rows])).all()}

    def activity(r: UserBookStatus) -> datetime:
        stamps = [d for d in (r.updated_at, positions.get(r.book_id)) if d is not None]
        return max(stamps) if stamps else datetime.min

    row = max(rows, key=activity)
    book = db.get(Book, row.book_id)
    if book is None:
        return None
    key = book_key(secret, book.id)
    segs = agg.segments_by_book.get(book.id, [])
    pct = max(0.0, min(100.0, round((row.progress_pct or 0.0) * 100, 1)))
    cur = {**_book_fields(book, key), "progress_pct": pct,
           "minutes_in": _minutes(agg.seconds_by_book.get(book.id, 0.0)),
           "started": min(s.day for s in segs).isoformat() if segs else None,
           "sittings": len(merge_sittings(segs))}
    _attach_cover(cur, book, key, sent, cover_keys)
    return cur


def build_envelope(db: Session, user: User, link: PlatformLink, *, full: bool,
                   since: date | None = None, today: date | None = None) -> Envelope:
    """The push body. ``full`` = every day since the first one; otherwise the
    days from (pushed_through - 1) or ``since``, whichever is earlier, to today,
    zeros included (tombstones for a day whose reading was deleted)."""
    tz = zone(link.timezone)
    _iid, secret = instance_identity(db)
    agg = aggregate(db, user, tz)
    today = today or datetime.now(timezone.utc).astimezone(tz).date()

    if full or link.pushed_through is None:
        start = agg.first_day
    else:
        start = link.pushed_through - timedelta(days=1)
        if since is not None:
            start = min(start, since)
    days: list[dict] = []
    cover_keys: set[str] = set()
    sent: set[str] = set()
    if link.share_titles:
        sent = {r.book_key for r in db.query(PlatformCoverSent).filter(PlatformCoverSent.user_id == user.id).all()}
    finished_books: dict[int, Book] = {}
    if link.share_titles:
        ids = {i for ids in agg.finished_by_day.values() for i in ids}
        if ids:
            finished_books = {b.id: b for b in db.query(Book).filter(Book.id.in_(ids)).all()}

    if start is not None:
        d = min(start, today)
        while d <= today:
            stats = agg.days.get(d) or {"minutes_read": 0, "minutes_by_type": {}, "sittings": 0,
                                        "longest_sitting_minutes": 0}
            done = agg.finished_by_day.get(d, [])
            entry = {"date": d.isoformat(), **stats, "books_finished": len(done)}
            if link.share_titles and done:
                items = []
                for book_id in done:
                    book = finished_books.get(book_id)
                    if book is None:
                        continue
                    key = book_key(secret, book_id)
                    item = {**_book_fields(book, key),
                            "minutes_total": _minutes(agg.seconds_by_book.get(book_id, 0.0))}
                    _attach_cover(item, book, key, sent, cover_keys)
                    items.append(item)
                entry["finished"] = items
            days.append(entry)
            d += timedelta(days=1)

    body: dict = {"schema_version": SCHEMA_VERSION, "timezone": link.timezone,
                  "share_titles": bool(link.share_titles), "days": days}
    if link.share_titles:
        body["current"] = _current(db, user, agg, tz, secret, sent, cover_keys)
    return Envelope(body=body, through=today, cover_keys=cover_keys)


def chunk_bodies(body: dict) -> list[dict]:
    """One body, or several when it is over ``BODY_BUDGET`` (Nightlamp's 8 MB
    limit, less headroom). Days are packed in order until the next one would
    pass the budget or ``CHUNK_DAYS``; covers make days uneven, so this goes by
    size, not count. ``current`` rides only on the last one (omitted =
    unchanged), and its size is reserved in every chunk."""
    def size(obj: Any) -> int:
        return len(json.dumps(obj, separators=(",", ":")))

    if size(body) <= BODY_BUDGET:
        return [body]
    base = {k: v for k, v in body.items() if k not in ("days", "current")}
    overhead = size({**base, "days": [], "current": body.get("current")})
    out: list[dict] = []
    chunk: list[dict] = []
    used = overhead
    for day in body["days"]:
        n = size(day) + 1  # the comma
        if chunk and (used + n > BODY_BUDGET or len(chunk) >= CHUNK_DAYS):
            out.append({**base, "days": chunk})
            chunk, used = [], overhead
        chunk.append(day)
        used += n
    out.append({**base, "days": chunk})
    if "current" in body:
        out[-1]["current"] = body["current"]
    return out


# ── HTTP ─────────────────────────────────────────────────────────────────────

class PlatformError(Exception):
    """Nightlamp could not be reached or answered with an error."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT)


def call(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None,
         token: str | None = None) -> dict:
    """One request to Nightlamp. Never logs the token or the body."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _client() as client:
            res = client.request(method, f"{base_url()}{path}", json=json_body, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise PlatformError(f"Could not reach {settings.platform_name} ({type(exc).__name__})") from None
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code >= 400:
        code = data.get("error") if isinstance(data, dict) else None
        raise PlatformError(f"{settings.platform_name} answered {res.status_code}"
                            + (f" ({code})" if code else ""), status=res.status_code, code=code)
    return data if isinstance(data, dict) else {}


# ── pushing ──────────────────────────────────────────────────────────────────

_user_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_running: set[int] = set()


def _lock_for(user_id: int) -> threading.Lock:
    with _locks_guard:
        return _user_locks.setdefault(user_id, threading.Lock())


def is_pushing(user_id: int) -> bool:
    return user_id in _running


def push_user(db: Session, user_id: int, *, full: bool = False, since: date | None = None) -> str:
    """Push one user's days. Returns "ok", "revoked", "error" or "skipped"."""
    if not enabled():
        return "skipped"
    lock = _lock_for(user_id)
    with lock:
        _running.add(user_id)
        try:
            return _push_locked(db, user_id, full=full, since=since)
        finally:
            _running.discard(user_id)


def _push_locked(db: Session, user_id: int, *, full: bool, since: date | None) -> str:
    link = db.query(PlatformLink).filter(PlatformLink.user_id == user_id).first()
    user = db.get(User, user_id)
    if link is None or user is None or link.revoked_at is not None:
        return "skipped"
    token = decrypt_secret(link.push_token_enc)
    if not token:
        link.last_push_error = "The stored token could not be read (server key changed?). Link again."
        db.commit()
        return "error"
    if full:
        db.query(PlatformCoverSent).filter(PlatformCoverSent.user_id == user_id).delete()
        db.flush()
    env = build_envelope(db, user, link, full=full, since=since)
    rejected_keys: set[str] = set()
    try:
        for body in chunk_bodies(env.body):
            res = call("POST", "/api/push", json_body=body, token=token)
            for r in res.get("rejected") or []:
                if isinstance(r, dict) and r.get("reason") == "cover_invalid" and r.get("key"):
                    rejected_keys.add(str(r["key"]))
    except PlatformError as exc:
        if exc.status == 401:
            link.revoked_at = datetime.utcnow()
            link.last_push_error = None
            db.commit()
            logger.info("platform push for user %s: link revoked on %s", user_id, settings.platform_name)
            return "revoked"
        link.last_push_error = str(exc)[:500]
        db.commit()
        logger.warning("platform push for user %s failed: %s", user_id, exc)
        return "error"
    for key in env.cover_keys - rejected_keys:
        db.add(PlatformCoverSent(user_id=user_id, book_key=key))
    link.last_push_at = datetime.utcnow()
    link.last_push_error = None
    link.pushed_through = env.through
    db.commit()
    return "ok"


def _session_factory() -> Session:
    from backend.core.database import SessionLocal
    return SessionLocal()


def _run(user_id: int, full: bool, since: date | None = None) -> None:
    db = _session_factory()
    try:
        push_user(db, user_id, full=full, since=since)
    except Exception:  # noqa: BLE001 - a push must never take anything down
        logger.exception("platform push for user %s crashed", user_id)
    finally:
        db.close()


def spawn_push(user_id: int, *, full: bool = False) -> None:
    """Push in a daemon thread (the request returns at once). Tests replace this."""
    threading.Thread(target=_run, args=(user_id, full), daemon=True, name=f"platform-push-{user_id}").start()


# ── dirty tracking + loop ────────────────────────────────────────────────────

_dirty: dict[int, tuple[float, datetime | None]] = {}  # user_id -> (monotonic marked, earliest UTC touched)
_dirty_guard = threading.Lock()


def mark_dirty(user_id: int, touched: datetime | None = None) -> None:
    if not enabled():
        return
    with _dirty_guard:
        _prev_at, prev_from = _dirty.get(user_id, (0.0, None))
        earliest = prev_from
        if touched is not None:
            t = _utc(touched)
            earliest = t if earliest is None else min(earliest, t)
        _dirty[user_id] = (_time.monotonic(), earliest)


def take_due(now: float | None = None, debounce: float = DEBOUNCE_SECONDS) -> dict[int, datetime | None]:
    now = _time.monotonic() if now is None else now
    with _dirty_guard:
        due = {u: frm for u, (at, frm) in _dirty.items() if now - at >= debounce}
        for u in due:
            _dirty.pop(u, None)
    return due


def clear_dirty() -> None:
    with _dirty_guard:
        _dirty.clear()


def tick(db: Session, *, now_mono: float | None = None, sweep: bool = False) -> list[int]:
    """Push debounced dirty users; with ``sweep``, also every linked user whose
    last push is over an hour old. Returns the user ids pushed."""
    due = take_due(now_mono)
    links = {l.user_id: l for l in db.query(PlatformLink).filter(PlatformLink.revoked_at.is_(None)).all()}
    pushed: list[int] = []
    for user_id, earliest in due.items():
        link = links.get(user_id)
        if link is None:
            continue
        since = earliest.astimezone(zone(link.timezone)).date() if earliest is not None else None
        push_user(db, user_id, since=since)
        pushed.append(user_id)
    if sweep:
        cutoff = datetime.utcnow() - timedelta(seconds=SWEEP_SECONDS)
        for user_id, link in links.items():
            if user_id in pushed:
                continue
            if link.last_push_at is None or link.last_push_at < cutoff:
                push_user(db, user_id)
                pushed.append(user_id)
    return pushed


async def platform_loop() -> None:
    """Every 30 s: push users whose reading changed over 2 minutes ago; once an
    hour: sweep every linked user."""
    last_sweep = 0.0

    def _tick(sweep: bool) -> None:
        db = _session_factory()
        try:
            tick(db, sweep=sweep)
        finally:
            db.close()

    await asyncio.sleep(TICK_SECONDS)
    while True:
        try:
            sweep = _time.monotonic() - last_sweep >= SWEEP_SECONDS
            await asyncio.to_thread(_tick, sweep)
            if sweep:
                last_sweep = _time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in platform push loop")
        await asyncio.sleep(TICK_SECONDS)


# Sessions are written in many places (TomeSync, the web reader, manual logs,
# trims, sync codes, imports). One flush hook notices them, and a status
# change to read/reading, and marks the user dirty after the commit.

_PENDING_KEY = "platform_dirty"


@event.listens_for(Session, "after_flush")
def _collect_dirty(session: Session, _ctx: Any) -> None:
    if not enabled():
        return
    try:
        pending: dict[int, datetime | None] = session.info.setdefault(_PENDING_KEY, {})

        def note(user_id: int | None, touched: datetime | None) -> None:
            if user_id is None:
                return
            prev = pending.get(user_id, None)
            if touched is None:
                pending.setdefault(user_id, None)
            else:
                pending[user_id] = touched if prev is None else min(prev, touched)

        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            if isinstance(obj, ReadingSession):
                note(obj.user_id, obj.started_at)
            elif isinstance(obj, UserBookStatus) and obj not in session.deleted:
                hist = sa_inspect(obj).attrs.status.history
                if hist.added and hist.added[0] in ("read", "reading"):
                    note(obj.user_id, None)
    except Exception:  # noqa: BLE001 - never break the write
        logger.exception("platform dirty hook failed")


@event.listens_for(Session, "after_commit")
def _flush_dirty(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    for user_id, touched in pending.items():
        mark_dirty(user_id, touched)


@event.listens_for(Session, "after_rollback")
def _drop_dirty(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


__all__ = [
    "PlatformError", "aggregate", "book_key", "build_envelope", "call", "chunk_bodies", "enabled",
    "instance_identity", "mark_dirty", "platform_loop", "push_user", "spawn_push", "split_by_midnight",
    "tick", "tome_user_id",
]
