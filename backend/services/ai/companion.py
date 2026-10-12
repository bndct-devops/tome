"""Reading companion: the reader's position, the text window, the glossary
payload and the gates shared by the web (``/api/ai``) and plugin
(``/api/tome-sync/ai``) endpoints.

The window. A synced position is a page fraction (KOReader) or a section
fraction (web reader), not the word fraction the chapter map uses, and it
runs ahead of it on books with images. ``book_text.reading_fraction`` turns it
into a word fraction ``w`` that is never past the reader, from the stored
xpointer or CFI when there is one. The reader is then in chapter
``k = book_text.chapter_for_fraction(w)`` (rounded DOWN; 0 when ``w`` is
before chapter 0). Everything the companion shows for that position is built
from:

- the digests of the chapters before ``k`` in the reader's scope (their read
  earlier volumes in full, then chapters ``0..k-1`` of this book), and
- for recap and live Recall, the text of chapter ``k`` cut at ``w``.

Nothing after ``w`` is ever read. A ``chapter`` asked for explicitly (web)
may not be past the reader's own position chapter.
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.core.permissions import is_member_or_above
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntityNote
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import digest, digest_job, settings as ai_settings
from backend.services.ai.provider import AIBudgetExceeded, AIError, AINotConfigured

RECAP = "recap"
RECALL = "recall"
DIGEST = digest.FEATURE

NO_CHAPTERS = book_text.NO_CHAPTERS
NOT_EPUB = book_text.NOT_EPUB
NO_POSITION = "no_position"
FEATURE_OFF = "feature_off"
NO_KEY = "no_key"

MAX_WINDOW_WORDS = 6000   # current-chapter text sent to recap and live Recall


class WindowError(Exception):
    """The requested window cannot be served. ``status_code`` + a plain
    English ``message`` for the API layer; ``reason`` is machine-readable."""

    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


class AlreadyRunning(AIError):
    """The same billed request is still in flight (a client retry after a
    proxy timeout): 409, nothing is called twice."""

    status_code = 409
    reason = "already_running"


_inflight: set[tuple[str, int, int]] = set()
_inflight_lock = threading.Lock()


@contextmanager
def in_flight(kind: str, user_id: int, obj_id: int, message: str) -> Iterator[None]:
    """At most one ``kind`` request per (user, object) at a time in this
    process; a second one raises ``AlreadyRunning`` with ``message``."""
    key = (kind, int(user_id), int(obj_id))
    with _inflight_lock:
        if key in _inflight:
            raise AlreadyRunning(message)
        _inflight.add(key)
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight.discard(key)


# ── position ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    pct: float                  # as synced (page or section fraction): for display
    source: str                 # "device" | "web"
    updated_at: datetime | None
    word_pct: float = 0.0       # the same position as a word fraction, never past it


def _position(book: Book, db: Session, pct: float, locator: str | None, source: str,
              updated_at: datetime | None) -> Position:
    pct = max(0.0, min(1.0, float(pct)))
    word_pct = book_text.reading_fraction(book, pct, locator, db)
    return Position(pct=pct, source=source, updated_at=updated_at,
                    word_pct=pct if word_pct is None else word_pct)


def reader_position(db: Session, user: User, book: Book) -> Position | None:
    """The user's latest position in ``book``: the synced position row (device
    or web reader), else the status row's progress (a book marked read counts
    as 1.0). None when there is neither."""
    pos = (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == user.id, TomeSyncPosition.book_id == book.id)
        .first()
    )
    if pos is not None and pos.percentage is not None:
        return _position(book, db, pos.percentage, pos.progress,
                         "web" if (pos.device or "") == "web" else "device", pos.updated_at)
    st = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book.id)
        .first()
    )
    if st is None:
        return None
    if st.status == "read":
        return Position(pct=1.0, source="web", updated_at=st.finished_at or st.updated_at,
                        word_pct=1.0)
    if st.progress_pct is not None and st.progress_pct > 0:
        return _position(book, db, st.progress_pct, st.cfi, "web", st.updated_at)
    return None


def position_chapter(book: Book, word_pct: float, db: Session) -> int:
    """Chapter a WORD fraction falls in, rounded down; 0 before chapter 0."""
    k = book_text.chapter_for_fraction(book, word_pct, db)
    return k if k is not None else 0


@dataclass
class Window:
    chapter_idx: int            # the chapter the reader is in (fold covers < this)
    until: float | None         # book fraction the chapter text is cut at; None = whole chapter
    chapters: list[BookChapter]
    position: Position | None

    @property
    def chapter(self) -> BookChapter | None:
        return next((c for c in self.chapters if c.idx == self.chapter_idx), None)

    @property
    def chapter_title(self) -> str | None:
        ch = self.chapter
        return ch.title if ch else None

    @property
    def finished(self) -> bool:
        """The whole book: the user marked it read (``chapter_idx`` is the
        chapter count, there is no current chapter)."""
        return self.chapter_idx >= len(self.chapters)

    @property
    def end_fraction(self) -> float:
        """Where the text this window may see ends (cache validity)."""
        ch = self.chapter
        if ch is None:
            return 1.0
        if self.until is not None:
            return min(self.until, ch.end_fraction)
        return ch.end_fraction


def is_read(db: Session, user: User | None, book: Book) -> bool:
    """The user marked ``book`` read: their window is the whole book."""
    if user is None:
        return False
    return db.query(UserBookStatus.id).filter(
        UserBookStatus.user_id == user.id, UserBookStatus.book_id == book.id,
        UserBookStatus.status == "read",
    ).first() is not None


def whole_window(chapters: list[BookChapter], position: Position | None) -> Window:
    """Every chapter of the book, no current chapter (a finished book)."""
    return Window(chapter_idx=len(chapters), until=None, chapters=chapters, position=position)


def web_window(db: Session, user: User, book: Book, chapter: int | None) -> Window:
    """The window for a web request: the stored position, or an explicit
    ``chapter`` at or before the position chapter. A book the user marked
    read is the whole book (``chapter_idx`` = chapter count, no current
    chapter), wherever its stored position sits. Raises WindowError."""
    _require_chapters(book, db)
    chapters = book_text.book_chapters(book, db)
    pos = reader_position(db, user, book)
    finished = is_read(db, user, book)
    if pos is None and not finished:
        raise WindowError(409, NO_POSITION, "There is no reading position for this book yet.")
    pos_k = len(chapters) if finished else position_chapter(book, pos.word_pct, db)  # type: ignore[union-attr]
    if chapter is None or chapter == pos_k:
        if finished:
            return whole_window(chapters, pos)
        return Window(chapter_idx=pos_k, until=pos.word_pct, chapters=chapters, position=pos)  # type: ignore[union-attr]
    if chapter < 0 or chapter >= len(chapters):
        raise WindowError(422, "bad_chapter", "This book has no such chapter.")
    if chapter > pos_k:
        raise WindowError(422, "past_position", "That chapter is past your reading position.")
    return Window(chapter_idx=chapter, until=None, chapters=chapters, position=pos)


def pct_window(db: Session, book: Book, pct: float, locator: str | None = None,
               user: User | None = None) -> Window:
    """The window for a fraction sent by a device, with its xpointer when it
    sends one (exact; without it the fraction is backed off by a margin).
    With ``user`` and the book marked read by them: the whole book.
    Raises WindowError."""
    _require_chapters(book, db)
    chapters = book_text.book_chapters(book, db)
    pos = _position(book, db, pct, locator, "device", None)
    if is_read(db, user, book):
        return whole_window(chapters, pos)
    return Window(chapter_idx=position_chapter(book, pos.word_pct, db), until=pos.word_pct,
                  chapters=chapters, position=pos)


def start_window(db: Session, user: User, book: Book) -> Window:
    """The window an explicit start (POST .../digest, "Write all",
    reconcile) reads up to: the stored position (``web_window``), or, when
    the book has no position yet (an unstarted next volume), a zero window
    at chapter 0: nothing of this book is read, the read earlier volumes are
    read in full. Raises WindowError for a book with no chapters."""
    try:
        return web_window(db, user, book, None)
    except WindowError as exc:
        if exc.reason != NO_POSITION:
            raise
    return Window(chapter_idx=0, until=None, chapters=book_text.book_chapters(book, db), position=None)


def _require_chapters(book: Book, db: Session) -> None:
    reason = book_text.unavailable_reason(book, db)
    if reason == NOT_EPUB:
        raise WindowError(422, NOT_EPUB, "The reading companion works with EPUB books only.")
    if reason == NO_CHAPTERS:
        raise WindowError(422, NO_CHAPTERS,
                          "This book has no chapter map yet. The word-count job in Admin builds it.")


def window_text(book: Book, window: Window, db: Session) -> str:
    """Chapter ``k``'s text up to the window's end, at most the last 6000 words
    (the part closest to the reader). "" for a finished book (no current
    chapter)."""
    if window.finished or window.chapter is None:
        return ""
    text = book_text.chapter_text(book, window.chapter_idx, db, until=window.until) or ""
    return book_text.truncate_words(text, MAX_WINDOW_WORDS, keep="tail")


# ── gates ────────────────────────────────────────────────────────────────────

def features_for(db: Session, user: User) -> dict[str, bool]:
    """What this user can use right now: ``recap`` and ``recall`` need AI on,
    the feature on and a key that resolves (recap also needs digests on);
    ``glossary`` is served from storage and needs AI and digests on."""
    if not ai_settings.ai_enabled(db) or not is_member_or_above(user):
        return {"recap": False, "recall": False, "glossary": False, "ask": False}
    has_key = ai_settings.resolve_key(db, user) is not None
    digests_on = ai_settings.feature_enabled(db, DIGEST)
    return {
        "recap": has_key and digests_on and ai_settings.feature_enabled(db, RECAP),
        "recall": has_key and ai_settings.feature_enabled(db, RECALL),
        "glossary": digests_on,
        # Ask reads chapters inside the window; it needs the digests (for the
        # summaries it shortlists with) and its own switch.
        "ask": has_key and digests_on and ai_settings.feature_enabled(db, "ask"),
    }


# ── glossary ─────────────────────────────────────────────────────────────────

def digest_rev(digests: list[AIChapterDigest], notes: str | list[int] | None = None) -> str:
    """Hash of the digest ids a fold ran over (and a stamp of the entity
    notes of those chapters): changes when a digest or a note is added, or a
    chapter is re-read."""
    ids = ",".join(str(i) for i in sorted(d.id for d in digests))
    if isinstance(notes, list):
        notes = ",".join(str(i) for i in sorted(notes))
    if notes:
        ids += "|" + notes
    return hashlib.sha256(ids.encode()).hexdigest()[:16]


def _window_note_stamp(db: Session, scope: digest.Scope, upto: int) -> str:
    """``count:max id`` of the entity notes inside the window (every note of
    the read earlier volumes, this book's notes before chapter ``upto``): one
    SQL aggregate, no rows loaded. A note added, removed or re-written moves
    it."""
    conds = [and_(AIEntityNote.book_id == scope.book.id, AIEntityNote.chapter_idx < upto)]
    if scope.included:
        conds.append(AIEntityNote.book_id.in_([b.id for b in scope.included]))
    count, top = db.query(func.count(AIEntityNote.id), func.max(AIEntityNote.id)).filter(or_(*conds)).one()
    return f"{int(count or 0)}:{int(top or 0)}" if count else ""


def window_rev(db: Session, digests: list[AIChapterDigest], scope: digest.Scope, upto: int) -> str:
    """``rev`` of a glossary folded over ``digests`` (digests + notes)."""
    return digest_rev(digests, _window_note_stamp(db, scope, upto))


def _chapter_titles(db: Session, book_ids: list[int]) -> dict[tuple[int, int], str]:
    if not book_ids:
        return {}
    rows = db.query(BookChapter.book_id, BookChapter.idx, BookChapter.title).filter(
        BookChapter.book_id.in_(book_ids))
    return {(b, i): t for b, i, t in rows}


def entries_to_dicts(db: Session, scope: digest.Scope, entries: list[digest.Entry]) -> list[dict[str, Any]]:
    """``Entry.to_dict()`` with ``first_seen``/``last_seen`` as ``{book_id,
    book_title, series_index, chapter_idx, chapter_title}`` (``series_index``
    None when the book has none)."""
    vols = scope.volumes()
    titles = _chapter_titles(db, [v.id for v in vols])
    book_titles = {v.id: v.title for v in vols}
    indexes = {v.id: v.series_index for v in vols}

    def where(loc: tuple[int, int]) -> dict[str, Any]:
        return {"book_id": loc[0], "book_title": book_titles.get(loc[0]),
                "series_index": indexes.get(loc[0]),
                "chapter_idx": loc[1], "chapter_title": titles.get(loc)}

    out = []
    for e in entries:
        d = e.to_dict()
        d["first_seen"] = where(e.first_seen)
        d["last_seen"] = where(e.last_seen)
        out.append(d)
    return out


TIMELINE_CAP = 3            # timeline lines per entry in the glossary LIST (the latest ones)
ENTRY_TIMELINE_CAP = 200    # timeline lines of one opened entry (``glossary_entry``)


def _profiles(db: Session, scope: digest.Scope, upto: int) -> list[Any]:
    """``digest.profiles`` when it exists, else [] (every entry then comes
    from the plain fold, with ``identity`` = its line and no facts)."""
    fn = getattr(digest, "profiles", None)
    return list(fn(db, scope, upto)) if fn is not None else []


def _full_entries(db: Session, scope: digest.Scope, upto: int,
                  digests: list[AIChapterDigest]) -> list[dict[str, Any]]:
    """Every entry of the window in the full v2 shape, timeline uncapped and
    places titled; ``glossary_entries`` and ``glossary_entry`` cut it down."""
    vols = scope.volumes()
    order = {v.id: n for n, v in enumerate(vols)}
    titles = _chapter_titles(db, [v.id for v in vols])
    indexes = {v.id: v.series_index for v in vols}
    folded = digest.fold_entries(digests)
    v1 = entries_to_dicts(db, scope, folded)
    profiles = [p.to_dict() for p in _profiles(db, scope, upto)]

    def place(book_id: int, chapter_idx: int) -> dict[str, Any]:
        return {"book_id": book_id, "series_index": indexes.get(book_id),
                "chapter_idx": chapter_idx, "chapter_title": titles.get((book_id, chapter_idx))}

    # A plain-fold entry joins a profile only of the same kind, by its
    # canonical name or an alias inside the window.
    by_kind_name: dict[tuple[str, str], int] = {}
    for n, p in enumerate(profiles):
        for nm in [p["name"]] + list(p.get("aliases") or []):
            key = digest.normalize_name(nm)
            if key:
                by_kind_name.setdefault((p["kind"], key), n)
    matched: dict[int, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []
    for d in v1:
        n = by_kind_name.get((d["kind"], digest.normalize_name(d["name"])))
        if n is None:
            unmatched.append(d)
        else:
            matched.setdefault(n, []).append(d)

    def rank(where: dict[str, Any] | None) -> tuple[int, int]:
        if not where:
            return (len(order), 0)
        return (order.get(where["book_id"], len(order)), where["chapter_idx"])

    out: list[dict[str, Any]] = []
    for n, p in enumerate(profiles):
        olds = matched.get(n, [])
        latest_old = max(olds, key=lambda d: rank(d["last_seen"]), default=None)
        timeline = [{**place(t["book_id"], t["chapter_idx"]), "line": t["line"]}
                    for t in p.get("timeline") or []]
        line = timeline[-1]["line"] if timeline else (latest_old["line"] if latest_old else "")
        identity = p.get("identity") or ""
        if p.get("entity_id") is not None and identity:
            more = identity
        else:
            more = next((d["more"] for d in olds if d["more"]), "")
        first = p.get("first_seen")
        last = p.get("last_seen")
        olds_first = min(olds, key=lambda d: rank(d["first_seen"]), default=None)
        # The earliest / latest of the profile and the plain fold: a chapter
        # read before entities existed never moves first_seen later.
        first_at = entries_where(db, scope, (first["book_id"], first["chapter_idx"]), titles) \
            if first else None
        last_at = entries_where(db, scope, (last["book_id"], last["chapter_idx"]), titles) \
            if last else None
        if olds_first and (first_at is None or rank(olds_first["first_seen"]) < rank(first_at)):
            first_at = olds_first["first_seen"]
        if latest_old and (last_at is None or rank(latest_old["last_seen"]) > rank(last_at)):
            last_at = latest_old["last_seen"]
        out.append({
            "name": p["name"], "kind": p["kind"], "line": line, "more": more,
            "evidence": latest_old["evidence"] if latest_old else "",
            "first_seen": first_at,
            "last_seen": last_at,
            "chapters": max([int(p.get("chapters") or 0)] + [int(d["chapters"]) for d in olds]),
            "entity_id": p.get("entity_id"),
            "aliases": list(p.get("aliases") or []),
            "identity": identity,
            "status": p.get("status"),
            "facts": {k: {"value": f["value"], **place(f["book_id"], f["chapter_idx"]),
                          "history": [{"value": h["value"], **place(h["book_id"], h["chapter_idx"])}
                                      for h in f.get("history") or []]}
                      for k, f in (p.get("facts") or {}).items()},
            "timeline": timeline,
        })
    for d in unmatched:
        last = d["last_seen"]
        out.append({
            **d, "entity_id": None, "aliases": [], "identity": d["line"], "status": None,
            "facts": {},
            "timeline": [{**place(last["book_id"], last["chapter_idx"]), "line": d["line"]}]
            if d["line"] else [],
        })
    out.sort(key=lambda e: rank(e["first_seen"]))   # stable: profile order within a chapter
    return out


def _capped(e: dict[str, Any], cap: int) -> dict[str, Any]:
    tl = e["timeline"]
    return {**e, "timeline": tl[-cap:] if cap else [], "timeline_truncated": len(tl) > cap}


def _untitled(where: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in where.items() if k != "chapter_title"}


def glossary_entries(db: Session, scope: digest.Scope, upto: int,
                     digests: list[AIChapterDigest]) -> list[dict[str, Any]]:
    """The glossary LIST: one entry per entity profile (``digest.profiles``),
    merged with the plain fold (``fold_entries``) by normalised name of the
    same kind (an entity's name or one of its aliases inside the window) so
    no entry of the old fold disappears. In order of first appearance.

    Each entry keeps the v1 fields (``name, kind, line, more, evidence,
    first_seen, last_seen, chapters``; ``line`` is the latest timeline line,
    ``more`` the identity when the entry is an entity with one, else the
    fold's; ``first_seen``/``last_seen`` titled) and adds ``entity_id,
    aliases, identity, status``, ``facts`` (``{field: {value, book_id,
    series_index, chapter_idx}}``, latest value inside the window, no
    ``history`` and no title strings), ``timeline`` (``[{book_id, series_index, chapter_idx, line}]``
    oldest first, the last ``TIMELINE_CAP``) and ``timeline_truncated``.
    The opened entry, titled and with the long timeline, is
    ``glossary_entry``."""
    out = []
    for e in _full_entries(db, scope, upto, digests):
        e = _capped(e, TIMELINE_CAP)
        # The list is loaded on every page open: no history here (the opened
        # entry, ``glossary_entry``, carries it).
        e["facts"] = {k: _untitled({h: v for h, v in f.items() if h != "history"})
                      for k, f in e["facts"].items()}
        e["timeline"] = [_untitled(t) for t in e["timeline"]]
        out.append(e)
    return out


def glossary_entry(db: Session, user: User, book: Book, window: Window, *,
                   entity_id: int | None = None, name: str | None = None,
                   kind: str | None = None) -> dict[str, Any] | None:
    """One entry of the window's glossary in the full v2 shape: facts and
    timeline places titled (``chapter_title``), the timeline's last
    ``ENTRY_TIMELINE_CAP`` lines. Found by ``entity_id``, else by ``name``
    (normalised; an entry with no entity, or an entity by name or alias) and
    ``kind`` when given. None when the window has no such entry."""
    scope = digest.user_scope(db, user, book)
    upto = window.chapter_idx
    entries = _full_entries(db, scope, upto, digest.scoped_digests(db, scope, upto))
    hit = None
    if entity_id is not None:
        hit = next((e for e in entries if e["entity_id"] == entity_id), None)
    elif name:
        key = digest.normalize_name(name)
        cands = [e for e in entries if not kind or e["kind"] == kind]
        hit = next((e for e in cands if digest.normalize_name(e["name"]) == key), None) or next(
            (e for e in cands if any(digest.normalize_name(a) == key for a in e["aliases"])), None)
    return _capped(hit, ENTRY_TIMELINE_CAP) if hit is not None else None


def entries_where(db: Session, scope: digest.Scope, loc: tuple[int, int],
                  titles: dict[tuple[int, int], str] | None = None) -> dict[str, Any]:
    """``{book_id, book_title, series_index, chapter_idx, chapter_title}`` of
    a place in ``scope`` (the shape ``entries_to_dicts`` gives
    ``first_seen``/``last_seen``)."""
    vols = scope.volumes()
    if titles is None:
        titles = _chapter_titles(db, [v.id for v in vols])
    book = next((v for v in vols if v.id == loc[0]), None)
    return {"book_id": loc[0], "book_title": book.title if book else None,
            "series_index": book.series_index if book else None,
            "chapter_idx": loc[1], "chapter_title": titles.get(loc)}


async def loose_json_body(request: Request) -> Any:
    """FastAPI dependency: the request's JSON body, or None when it is
    missing or not JSON. A route that validates its own body uses it so
    every bad body is a 422 with a plain ``detail`` message, never
    pydantic's error list."""
    try:
        raw = await request.body()
        if not raw:
            return None
        import json
        return json.loads(raw)
    except ValueError:
        return None


GZIP_MIN_BYTES = 16_384


def json_response(payload: Any, accept_encoding: str | None) -> Any:
    """``payload`` as a JSON response, gzipped when the client accepts gzip
    and the body is large (a long series' glossary list is about a megabyte
    of very repetitive JSON; compressed it is a tenth of that). Clients that
    do not send ``Accept-Encoding: gzip`` get plain JSON."""
    import gzip
    import json

    from starlette.responses import Response

    body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    headers = {"Vary": "Accept-Encoding"}
    if len(body) >= GZIP_MIN_BYTES and "gzip" in (accept_encoding or "").lower():
        body = gzip.compress(body, compresslevel=6)
        headers["Content-Encoding"] = "gzip"
    return Response(content=body, media_type="application/json", headers=headers)


def glossary_payload(db: Session, user: User, book: Book, window: Window) -> dict[str, Any]:
    """The fold for ``window`` with scope, coverage, the active job (if any)
    and ``rev``. Read-only: never queues work (``start_digest`` does).

    ``entries`` are ``glossary_entries`` (v2: the v1 fields plus the entity
    profile), in order of first appearance; ``first_seen``/``last_seen`` are
    ``{book_id, book_title, series_index, chapter_idx, chapter_title}``.
    ``rev`` changes when a digest or an entity note in the window changes."""
    scope = digest.user_scope(db, user, book)
    upto = window.chapter_idx
    digests = digest.scoped_digests(db, scope, upto)
    cov = digest.coverage(db, scope, upto)
    out = glossary_entries(db, scope, upto, digests)
    return {
        "book_id": book.id,
        "chapter_idx": upto,
        "chapter_title": window.chapter_title,
        "entries": out,
        "count": len(out),
        "scope": scope.to_dict(),
        "coverage": cov,
        "job": digest_job.active_for(user.id, book.id),
        "rev": window_rev(db, digests, scope, upto),
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt is not None else None


def _with_places(db: Session, scope: digest.Scope, book: Book, upto: int,
                 entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``entries`` with where each alias and the identity were learned, so a
    device reading behind the snapshot's position (a re-read) can hide them:
    ``alias_places`` ``[{name, book_id, series_index, chapter_idx}]`` (one per
    alias in ``aliases`` whose learning place inside the window is known; the
    earliest one) and ``identity_at`` ``{book_id, series_index, chapter_idx}``
    (the entry's ``first_seen``: the identity is written at the first sight)."""
    from backend.models.ai_entity import AIEntity

    vols = scope.volumes()
    order = {v.id: n for n, v in enumerate(vols)}
    indexes = {v.id: v.series_index for v in vols}
    ids = [e["entity_id"] for e in entries if e.get("entity_id") is not None and e.get("aliases")]
    ents = {e.id: e for e in db.query(AIEntity).filter(AIEntity.id.in_(ids)).all()} if ids else {}

    def inside(a: dict[str, Any]) -> bool:
        bid, ci = a.get("book_id"), a.get("chapter_idx")
        if bid not in order or not isinstance(ci, int) or isinstance(ci, bool):
            return False
        return bid != book.id or ci < upto

    out = []
    for e in entries:
        places = []
        ent = ents.get(e.get("entity_id")) if e.get("entity_id") is not None else None
        if ent is not None:
            items = [a for a in digest.entity_aliases(ent) if inside(a)]
            for name in e.get("aliases") or []:
                key = digest.normalize_name(name)
                hits = [a for a in items if digest.normalize_name(a["name"]) == key]
                if not hits:
                    continue
                a = min(hits, key=lambda a: (order[a["book_id"]], a["chapter_idx"]))
                places.append({"name": name, "book_id": a["book_id"],
                               "series_index": indexes.get(a["book_id"]),
                               "chapter_idx": a["chapter_idx"]})
        first = e.get("first_seen")
        identity_at = ({"book_id": first.get("book_id"), "series_index": first.get("series_index"),
                        "chapter_idx": first.get("chapter_idx")} if isinstance(first, dict) else None)
        out.append({**e, "alias_places": places, "identity_at": identity_at})
    return out


def snapshot_payload(db: Session, user: User, book: Book, window: Window) -> dict[str, Any]:
    """The offline snapshot a device keeps for ``book`` (KOReader, run 2b):
    the glossary list of ``window`` (``glossary_payload``'s ``entries`` and
    ``rev``), the chapter table (so the device can place a page fraction and
    hide what lies past a re-read position), the ready recaps of the user's
    read earlier volumes and the cached recap at the window when there is a
    fresh one. Nothing past the window; read-only, never queues work and
    never calls a model.

    ``{rev, as_of: {chapter_idx, pct}, book: {id, title, series,
    series_index, series_key}, chapters: [{idx, title, start_fraction,
    end_fraction}], entries, recaps: {volumes: [{book_id, title,
    series_index, text, created_at}], current: {text, chapter_idx,
    created_at} | None}, generated_at}``. ``as_of.pct`` is the position as
    synced (None for the zero window of a book with no position). Each
    entry also carries ``alias_places`` and ``identity_at``
    (``_with_places``)."""
    from backend.services.ai import recap, volume_recap

    gloss = glossary_payload(db, user, book, window)
    # Not ``volumes_payload``: that also prices Write all and scans for
    # reconcile candidates, none of which the device shows.
    vols = [volume_recap.item(volume_recap.state(db, user, v))
            for v in volume_recap.included_volumes(db, user, book)]
    row = recap.cached_for(db, user, book, window)
    scope = digest.user_scope(db, user, book)
    entries = _with_places(db, scope, book, gloss["chapter_idx"], gloss["entries"])
    return {
        "rev": gloss["rev"],
        "as_of": {"chapter_idx": gloss["chapter_idx"],
                  "pct": window.position.pct if window.position is not None else None},
        "book": {"id": book.id, "title": book.title, "series": book.series,
                 "series_index": book.series_index, "series_key": digest.series_key(book)},
        "chapters": [{"idx": c.idx, "title": c.title, "start_fraction": c.start_fraction,
                      "end_fraction": c.end_fraction} for c in window.chapters],
        "entries": entries,
        "recaps": {
            "volumes": [{k: v[k] for k in ("book_id", "title", "series_index", "text", "created_at")}
                        for v in vols if v["status"] == volume_recap.READY and v["text"]],
            "current": {"text": row.text, "chapter_idx": row.chapter_idx,
                        "created_at": _iso(row.created_at)} if row is not None else None,
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


# ── starting a digest job (the explicit "read these chapters first") ─────────

DIGEST_SCOPES = ("book", "series")


def _live_jobs_pending(user: User, book: Book) -> float:
    """USD the user's other live jobs are still expected to spend (estimate
    minus spent so far, floored at 0). A live job on this book is left out:
    a new start coalesces into it."""
    total = 0.0
    for snap in digest_job.running(user.id):
        if snap.get("book_id") == book.id:
            continue
        total += max(0.0, (snap.get("est_cost_usd") or 0.0) - (snap.get("cost_usd") or 0.0))
    return total


def _less_pending(est: dict[str, Any] | None, pending: float) -> dict[str, Any] | None:
    """``est`` with ``remaining_budget_usd``/``over_budget`` net of what
    the user's other live jobs will still spend."""
    if est is None or pending <= 0 or est.get("remaining_budget_usd") is None:
        return est
    left = max(0.0, est["remaining_budget_usd"] - pending)
    return {**est, "remaining_budget_usd": round(left, 4),
            "over_budget": est["est_cost_usd"] > left + 1e-9}


def digest_upto(db: Session, user: User, book: Book, window: Window) -> int:
    """The chapter an explicit digest start (and its estimate) reads up to:
    the window's chapter. A book the user marked read already has the whole
    book as its window (``chapter_idx`` = chapter count, see ``web_window``),
    so every chapter is read; the read check stays for a window built
    without the user (a device fraction short of the end, 97%)."""
    if window.finished or not is_read(db, user, book):
        return window.chapter_idx
    return len(window.chapters)


def start_digest(db: Session, user: User, book: Book, window: Window,
                 scope: str = "book",
                 followups: tuple[str, ...] = (),
                 kind: str = "digest") -> tuple[int, dict[str, Any]]:
    """(HTTP status, body) for an explicit start: digest the missing chapters
    before the window's chapter, of this book only (``scope="book"``) or of
    the read earlier volumes too (``"series"``).

    202 ``{job, estimate, coverage, scope, chapter_idx, chapter_title}``
    with the queued (or coalesced) job; 200 with ``job`` = the active job or
    None when nothing in the window is missing. Raises ``AIDisabled``
    (digests off), ``AINotConfigured`` (no key) and ``AIBudgetExceeded``
    (409 ``reason: "budget"``: the estimate is over what is left of a
    monthly limit) before anything is queued. A user-started job becomes
    the latest for the book, which lets the automatic triggers act again
    after a cancel. ``window`` may be the zero window of ``start_window``
    (no position: the read earlier volumes only). ``followups`` are the
    job's steps after reading (``digest_job`` follow-up names; Read again
    passes the merge step). ``kind`` goes on the job row ("reread" for Read
    again)."""
    from backend.services.ai import budget, estimate

    ai.check_feature_available(db, DIGEST)
    if ai_settings.resolve_key(db, user) is None:
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    if scope not in DIGEST_SCOPES:
        scope = "book"
    sc = digest.Scope(book=book) if scope == "book" else digest.user_scope(db, user, book)
    upto = digest_upto(db, user, book, window)
    body: dict[str, Any] = {
        "coverage": digest.coverage(db, sc, upto), "scope": scope,
        "chapter_idx": upto, "chapter_title": window.chapter_title,
    }
    if not digest.missing_chapters(db, sc, upto):
        return 200, {"job": digest_job.active_for(user.id, book.id), "estimate": None, **body}
    pending = _live_jobs_pending(user, book)
    est = _less_pending(estimate.estimate(db, user, book, scope, upto), pending)
    if est["over_budget"]:
        b = budget.status(db, user)
        err = b.exceeded_by(est["est_cost_usd"] + pending)
        if err is not None:
            raise AIBudgetExceeded(
                f"Reading these chapters costs about {est['est_cost_usd']:.2f} USD, more than the "
                f"{est['remaining_budget_usd']:.2f} USD left of the monthly AI budget "
                f"({err.limit:.2f} USD). Raise it in Settings > AI.",
                which=err.which, spent=err.spent, limit=err.limit)
    job = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=upto,
                             current_book_only=(scope == "book"), followups=followups,
                             kind=kind)
    if job.get("expanding") or not job.get("total"):
        # The worker has not planned the job yet: show the estimate's figures
        # rather than "0 of 0" until the first poll.
        job = {**job, "total": est["chapters"], "est_chapters": est["chapters"],
               "est_cost_usd": est["est_cost_usd"], "est_seconds": est["est_seconds"]}
    return 202, {"job": job, "estimate": est, **body}


def digest_estimates(db: Session, user: User, book: Book, window: Window) -> dict[str, Any]:
    """``{book, series, chapter_idx, chapter_title}``: what digesting the
    missing chapters before the window's chapter would cost and take, for
    this book only and with the read earlier volumes (``series`` None when
    there are none). See ``estimate.estimates``."""
    from backend.services.ai import estimate

    ai.check_feature_available(db, DIGEST)
    both = estimate.estimates(db, user, book, digest_upto(db, user, book, window))
    pending = _live_jobs_pending(user, book)
    return {"book": _less_pending(both["book"], pending), "series": _less_pending(both["series"], pending),
            "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title}


def glossary_rev(db: Session, user: User, book: Book, pct: float,
                 locator: str | None = None) -> str | None:
    """``rev`` of the glossary at ``pct`` (with the device's xpointer) for the
    plugin's position response; None when the glossary is off for this user
    or the book has no chapters."""
    if not features_for(db, user)["glossary"]:
        return None
    if book_text.unavailable_reason(book, db):
        return None
    scope = digest.user_scope(db, user, book)
    if is_read(db, user, book):
        upto = len(book_text.book_chapters(book, db))
    else:
        word_pct = book_text.reading_fraction(book, pct, locator, db) or 0.0
        upto = position_chapter(book, word_pct, db)
    return window_rev(db, digest.scoped_digests(db, scope, upto), scope, upto)
