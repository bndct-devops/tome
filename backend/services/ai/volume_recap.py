"""Recap as volumes (feature ``recap``): one recap per finished earlier volume.

A volume recap is user-independent: it is written from that volume's own
stored chapter SUMMARIES (``digest.Scope(book=vol)``, every chapter), never
from chapter text, and stored in ``ai_recaps`` as a whole-book row
(``chapter_idx == chapter count``, ``position_fraction = 1.0``) under the
volume's own ``scope_hash``. That is the same shape the finished-book hook
("Previously on", ``recap._previously_followup``) writes, so a row it wrote
for a reader with no earlier volumes is already a ready volume recap. When
the volume has no own-scope row but the hook wrote one for THIS user's scope
of it (earlier volumes folded in), that row is shown instead (nothing already
paid for is wasted); "Write it" / "Ask again" always writes the own-scope row.

The list is the user's read earlier volumes in the window of the open book
(``digest.user_scope(...).included``, reading order). Reading it never writes
and never queues; writing is one ``recap`` call per volume through
``ai.run_feature`` (budget-checked, one usage row).
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.ai_recap import AIRecap
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, recap, settings as ai_settings
from backend.services.ai.provider import (
    AIBudgetExceeded, AIDisabled, AIError, AIKeyInvalid, AINotConfigured, AIProviderError,
)

log = logging.getLogger(__name__)

FEATURE = recap.FEATURE
MAX_TOKENS = 16_000

VOLUME_PROMPT = """\
You write the recap of one finished volume of a book series, for a reader \
who has read it and wants to remember what happened before going on.

You receive the chapter summaries of that volume, in order. They are all \
you know about it.

Write 4 to 6 short paragraphs covering the whole volume, beginning to end:
- Past tense throughout. The last paragraph is about how the volume ends.
- Plain and factual. No judgement of characters or of the book, no \
foreshadowing, no hints, nothing about what comes in later volumes, nothing \
about the book's reputation.
- Use only the supplied summaries. Even if you recognise the book, never use \
what you know about it from elsewhere. If something is not in the summaries, \
it did not happen.
- Names as the summaries write them. Plain hyphens, never em-dashes.

Return only the JSON the schema describes: the paragraphs in order."""

READY, PARTIAL, MISSING = "ready", "partial", "missing"


@dataclass
class VolumeState:
    vol: Book
    chapters: int                  # chapter count of the volume
    coverage: dict[str, int]       # digest.coverage of its own scope, all chapters
    row: AIRecap | None            # the row shown (own scope, else the user's scope)
    own: bool = True               # ``row`` is the own-scope row
    fresh: bool = False            # ``row`` was written from every usable digest there is now


# ── lookup ───────────────────────────────────────────────────────────────────

def own_scope(vol: Book) -> digest.Scope:
    return digest.Scope(book=vol)


def included_volumes(db: Session, user: User, book: Book) -> list[Book]:
    """The user's read earlier volumes in the window of ``book``, in reading
    order (visibility and readability checked by ``user_scope``)."""
    return list(digest.user_scope(db, user, book).included)


def has_series_recaps(db: Session, user: User, book: Book) -> bool:
    return bool(included_volumes(db, user, book))


def _own_row(db: Session, vol: Book, n: int) -> AIRecap | None:
    return recap.get_cached(db, vol.id, n, own_scope(vol).scope_hash)


def state(db: Session, user: User, vol: Book) -> VolumeState:
    """The volume's own coverage and the row shown for it: the own-scope row,
    else the row the finished-book hook wrote for this user's scope of it.
    ``fresh``: no usable digest was added since the row was written (for the
    user-scope row, counted in that scope)."""
    n = len(book_text.book_chapters(vol, db))
    own = own_scope(vol)
    cov = digest.coverage(db, own, n)
    row = _own_row(db, vol, n) if n else None
    if row is not None:
        return VolumeState(vol=vol, chapters=n, coverage=cov, row=row, own=True,
                           fresh=row.digested >= digest.usable(cov))
    if n:
        user_sc = digest.user_scope(db, user, vol)
        if user_sc.scope_hash != own.scope_hash:
            row = recap.get_cached(db, vol.id, n, user_sc.scope_hash)
            if row is not None:
                fresh = row.digested >= digest.usable(digest.coverage(db, user_sc, n))
                return VolumeState(vol=vol, chapters=n, coverage=cov, row=row, own=False, fresh=fresh)
    return VolumeState(vol=vol, chapters=n, coverage=cov, row=None)


def _status(row: AIRecap | None) -> str:
    if row is None:
        return MISSING
    return READY if row.digested >= row.needed else PARTIAL


def item(st: VolumeState) -> dict[str, Any]:
    """``{book_id, title, series_index, chapters, status, digested, needed,
    text, created_at, missing_chapters}``. ``digested``/``needed`` are the
    row's (what it was written from), or the volume's coverage now when there
    is no row; ``missing_chapters`` is the volume's chapters with no digest
    yet, now."""
    row, cov = st.row, st.coverage
    return {
        "book_id": st.vol.id,
        "title": st.vol.title,
        "series_index": st.vol.series_index,
        "chapters": st.chapters,
        "status": _status(row),
        "digested": row.digested if row is not None else digest.usable(cov),
        "needed": row.needed if row is not None else cov["needed"],
        "text": row.text if row is not None else None,
        "created_at": row.created_at.isoformat() + "Z" if row is not None and row.created_at else None,
        "missing_chapters": max(0, cov["needed"] - cov["digested"]),
    }


def can_write(db: Session, user: User) -> bool:
    return ai_settings.resolve_key(db, user) is not None


def volumes_payload(db: Session, user: User, book: Book) -> dict[str, Any]:
    """GET .../recap/volumes. Read-only; works without a position."""
    vols = included_volumes(db, user, book)
    current: dict[str, Any] = {"book_id": book.id, "title": book.title,
                               "series_index": book.series_index,
                               "has_position": False, "recap_cached": False}
    pos = companion.reader_position(db, user, book)
    if pos is not None and not book_text.unavailable_reason(book, db):
        current["has_position"] = True
        try:
            window = companion.web_window(db, user, book, None)
        except companion.WindowError:
            window = None
        if window is not None:
            current["recap_cached"] = recap.cached_for(db, user, book, window) is not None
    states = [state(db, user, v) for v in vols]
    return {
        "can_write": can_write(db, user),
        "volumes": [item(st) for st in states],
        "current": current,
        "write_all": {**write_all_figures(db, states), **run_state(user.id, book.id)},
    }


def find_volume(db: Session, user: User, book: Book, vol_id: int) -> Book | None:
    return next((v for v in included_volumes(db, user, book) if v.id == vol_id), None)


# ── write ────────────────────────────────────────────────────────────────────

def build_user_content(db: Session, vol: Book, n: int) -> tuple[list[dict[str, Any]], int]:
    """(content blocks, summaries used): the volume's chapter summaries only."""
    summaries = digest.scoped_digests(db, own_scope(vol), n)
    titles = companion._chapter_titles(db, [vol.id])
    lines: list[str] = []
    for d in summaries:
        s = (d.summary or "").strip()
        if not s:
            continue
        title = titles.get((d.book_id, d.chapter_idx)) or f"Chapter {d.chapter_idx + 1}"
        lines.append(f"### {title}\n{s}\n")
    context = {"book": vol.title, "series": vol.series, "volume": vol.series_index,
               "chapters": n, "summaries": len(lines)}
    blocks = [
        {"type": "text", "text": f"Chapter summaries of {recap._volume_label(vol)}, in order:\n\n"
                                 + "\n".join(lines)},
        {"type": "text", "text": "Write the recap of this volume. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)},
    ]
    return blocks, len(lines)


def generate_volume(db: Session, user: User, vol: Book) -> AIRecap:
    """Write (or rewrite) the own-scope recap of ``vol`` from the summaries
    that exist now: one ``recap`` call. Raises ``recap.NothingToRecap`` with no
    summary at all, and the typed ``AIError``s from ``run_feature``."""
    ai.check_feature_available(db, FEATURE)
    n = len(book_text.book_chapters(vol, db))
    scope = own_scope(vol)
    cov = digest.coverage(db, scope, n)
    content, used = build_user_content(db, vol, n)
    if not used:
        raise recap.NothingToRecap()
    from backend.services.ai import estimate
    guess = estimate.content_cost_guess(FEATURE, ai_settings.effective_model(db, FEATURE), content,
                                        MAX_TOKENS)
    result = ai.run_feature(db, user, FEATURE, system=VOLUME_PROMPT, user_content=content,
                            schema=recap.build_schema(), max_tokens=MAX_TOKENS, book_id=vol.id,
                            cost_guess=guess)
    text = recap._clean_paragraphs(result.parsed)
    if not text:
        raise AIProviderError("The model returned an empty recap.")
    fields = dict(
        position_fraction=1.0, text=text, model=result.model,
        digested=digest.usable(cov), needed=cov["needed"],
        missing_json=json.dumps(recap.missing_volumes(db, scope, n), ensure_ascii=False),
        input_tokens=result.input_tokens + result.cache_read_tokens + result.cache_write_tokens,
        output_tokens=result.output_tokens,
    )
    row = _own_row(db, vol, n)
    if row is None:
        row = AIRecap(book_id=vol.id, chapter_idx=n, scope_hash=scope.scope_hash, **fields)
        db.add(row)
    else:
        for name, value in fields.items():
            setattr(row, name, value)
        row.created_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = _own_row(db, vol, n)
        if row is None:
            raise
    return row


def request_volume(db: Session, user: User, vol: Book, *, force: bool = False,
                   again: bool = False) -> tuple[int, dict[str, Any]]:
    """(HTTP status, body) for POST .../recap/volumes/{vol_id}.

    No call when the shown row exists and no usable digest was added since it
    was written (``fresh``), ``force`` or not: 200 with the volume item, or
    202 ``{needs_digest: true, estimate, coverage}`` when chapters are still
    undigested and not ``force`` (nothing is queued; the client starts POST
    /books/{vol_id}/digest {scope: "book"}). A call only when usable digests
    grew since the row (or there is no row), or on ``again``: "Ask again"
    first deletes the volume's failed placeholders (so a digest job tries
    those chapters once more), then rewrites the own-scope row in place once
    the call succeeds; the user-scope row of the finished-book hook ("Previously
    on") is never touched. ``force`` writes a partial recap from the summaries
    that exist. One request per (user, volume) at a time: a second is a 409
    ``already_running``."""
    ai.check_feature_available(db, FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    if not can_write(db, user):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    with companion.in_flight("volume_recap", user.id, vol.id,
                             "This recap is already being written."):
        if again:
            n = len(book_text.book_chapters(vol, db))
            if digest.clear_failed(db, own_scope(vol), n):
                db.commit()
        st = state(db, user, vol)
        cov = st.coverage
        undigested = cov["digested"] < cov["needed"]
        if undigested and not force:
            from backend.services.ai import estimate
            return 202, {"needs_digest": True,
                         "estimate": estimate.estimate(db, user, vol, "book", st.chapters),
                         "coverage": cov}
        if st.row is not None and st.fresh and not again:
            return 200, item(st)
        generate_volume(db, user, vol)
        return 200, item(state(db, user, vol))


# ── write all ────────────────────────────────────────────────────────────────

UNREAD, FRESH, BUDGET, ERROR = "unread", "fresh", "budget", "error"


def skip_reason(st: VolumeState) -> str | None:
    """Why "Write all" leaves ``st`` alone, or None when it writes it:
    ``fresh`` (the shown row is current), ``unread`` (chapters with no digest
    yet: writing all never queues reading)."""
    if st.row is not None and st.fresh:
        return FRESH
    cov = st.coverage
    if cov["digested"] < cov["needed"]:
        return UNREAD
    return None


def _summary_words(db: Session, vol: Book, n: int) -> int:
    content, _ = build_user_content(db, vol, n)
    return sum(len(str(b.get("text", "")).split()) for b in content)


def write_all_figures(db: Session, states: list[VolumeState]) -> dict[str, Any]:
    """``{count, est_cost_usd}``: how many volumes "Write all" would write now
    and the sum of one conservative ``recap`` call guess per volume (its
    summaries block, output at ``MAX_TOKENS``), rounded to cents."""
    from backend.services.ai import estimate
    model = ai_settings.effective_model(db, FEATURE)
    count, cost = 0, 0.0
    for st in states:
        if skip_reason(st) is not None:
            continue
        count += 1
        cost += estimate.call_cost_guess(FEATURE, model, _summary_words(db, st.vol, st.chapters),
                                         max_tokens=MAX_TOKENS, cached_words=0)
    return {"count": count, "est_cost_usd": round(cost, 2) if count else 0.0}


def _skip(st: VolumeState, reason: str, message: str | None = None) -> dict[str, Any]:
    cov = st.coverage
    out: dict[str, Any] = {"book_id": st.vol.id, "title": st.vol.title,
                           "series_index": st.vol.series_index, "reason": reason,
                           "missing_chapters": max(0, cov["needed"] - cov["digested"])}
    if message:
        out["message"] = message
    return out


# "Write all" runs in a daemon thread (one Opus call per volume is up to a
# minute; seven of them outlive any proxy timeout). The run holds the
# ``volume_recap_all`` in-flight guard for its whole life; its outcome is kept
# in memory per (user, book) and shown by GET (``write_all.running`` /
# ``write_all.last``). Single process, like the digest jobs.

_runs_lock = threading.Lock()
_runs: dict[tuple[int, int], dict[str, Any]] = {}
# Queued runs not started yet: key -> the entered in-flight guard.
_pending: dict[tuple[int, int], AbstractContextManager[None]] = {}

# Indirection for tests: the session factory and how a run is started
# (tests replace ``_spawn`` with ``lambda: None`` and call ``run_now``).
_session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal


def _start_thread() -> threading.Thread:
    t = threading.Thread(target=run_pending, name="ai-volume-recaps", daemon=True)
    t.start()
    return t


_spawn: Callable[[], Optional[threading.Thread]] = _start_thread


def reset() -> None:
    """Forget every run (tests)."""
    with _runs_lock:
        pending = list(_pending.values())
        _pending.clear()
        _runs.clear()
    for guard in pending:
        guard.__exit__(None, None, None)


def run_state(user_id: int, book_id: int) -> dict[str, Any]:
    """``{running, last}`` for GET's ``write_all``: ``last`` is the most
    recent run for this user and book, ``{started, written: [book_id],
    skipped, finished_at}`` (``finished_at`` null while it runs; ``written``
    grows as it goes), or null."""
    with _runs_lock:
        run = _runs.get((int(user_id), int(book_id)))
        if run is None:
            return {"running": False, "last": None}
        finished = run["finished_at"]
        return {"running": bool(run["running"]),
                "last": {"started": run["started"], "written": list(run["written"]),
                         "skipped": [dict(s) for s in run["skipped"]],
                         "finished_at": finished.isoformat() + "Z" if finished else None}}


def write_all(db: Session, user: User, book: Book) -> dict[str, Any]:
    """POST .../recap/volumes ("Write all"): start writing, one after another
    in a background thread, every included earlier volume whose shown row is
    missing or stale and that has no undigested chapter, and return at once
    ``{started: <how many it will try>, volumes: <the GET payload>}`` (202).
    Never queues reading. A missing key raises here; one request per (user,
    book) at a time: a second while the run lasts is a 409
    ``already_running``. See ``_run`` for the loop."""
    ai.check_feature_available(db, FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    if not can_write(db, user):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    guard = companion.in_flight("volume_recap_all", user.id, book.id,
                                "These recaps are already being written.")
    guard.__enter__()
    try:
        states = [state(db, user, v) for v in included_volumes(db, user, book)]
        started = sum(1 for st in states if skip_reason(st) is None)
        key = (int(user.id), int(book.id))
        with _runs_lock:
            _runs[key] = {"running": True, "started_at": datetime.utcnow(), "finished_at": None,
                          "started": started, "written": [], "skipped": []}
            _pending[key] = guard
    except BaseException:
        guard.__exit__(None, None, None)
        raise
    payload = volumes_payload(db, user, book)
    _spawn()
    return {"started": started, "volumes": payload}


def run_pending() -> None:
    """Run every queued "Write all" in this thread (the daemon thread's body)."""
    while True:
        with _runs_lock:
            if not _pending:
                return
            key = next(iter(_pending))
        run_now(*key)


def run_now(user_id: int, book_id: int) -> None:
    """Run the queued "Write all" for (user, book) in the calling thread (the
    worker, or a test). No-op when none is queued."""
    key = (int(user_id), int(book_id))
    with _runs_lock:
        guard = _pending.pop(key, None)
    if guard is None:
        return
    try:
        with _session_factory() as db:
            _run(db, key)
    except Exception as exc:   # never leave a run "running"
        log.exception("Write all failed for user %s book %s", *key)
        _record(key, skipped={"book_id": key[1], "title": "", "series_index": None,
                              "reason": ERROR, "missing_chapters": 0,
                              "message": getattr(exc, "message", None) or str(exc)})
    finally:
        with _runs_lock:
            run = _runs.get(key)
            if run is not None:
                run["running"] = False
                run["finished_at"] = datetime.utcnow()
        guard.__exit__(None, None, None)


def _record(key: tuple[int, int], *, written: int | None = None,
            skipped: dict[str, Any] | None = None) -> None:
    with _runs_lock:
        run = _runs.get(key)
        if run is None:
            return
        if written is not None:
            run["written"].append(written)
        if skipped is not None:
            run["skipped"].append(skipped)


def _run(db: Session, key: tuple[int, int]) -> None:
    """The loop. Each write is ``generate_volume`` under the per-volume
    in-flight guard (budget-checked per call); ``AIBudgetExceeded`` stops it
    (the volumes written so far stay; the rest are skipped as ``budget`` with
    the server message); a missing, invalid or disabled key stops it the same
    way as ``error``; any other model failure skips that volume as ``error``
    and goes on. A volume being written on its own is skipped as ``error``."""
    user = db.get(User, key[0])
    book = db.get(Book, key[1])
    if user is None or book is None:
        return
    stop_reason: str | None = None
    stop_msg: str | None = None
    for vol in included_volumes(db, user, book):
        st = state(db, user, vol)
        reason = skip_reason(st)
        if reason is not None:
            _record(key, skipped=_skip(st, reason))
            continue
        if stop_reason is not None:
            _record(key, skipped=_skip(st, stop_reason, stop_msg))
            continue
        try:
            with companion.in_flight("volume_recap", user.id, vol.id,
                                     "This recap is already being written."):
                generate_volume(db, user, vol)
        except AIBudgetExceeded as exc:
            db.rollback()
            stop_reason, stop_msg = BUDGET, exc.message
            _record(key, skipped=_skip(st, BUDGET, stop_msg))
            continue
        except (AINotConfigured, AIKeyInvalid, AIDisabled) as exc:
            db.rollback()
            stop_reason, stop_msg = ERROR, getattr(exc, "message", None) or str(exc)
            _record(key, skipped=_skip(st, ERROR, stop_msg))
            continue
        except AIError as exc:   # incl. companion.AlreadyRunning
            db.rollback()
            _record(key, skipped=_skip(st, ERROR, getattr(exc, "message", None) or str(exc)))
            continue
        _record(key, written=vol.id)


def whole_book(db: Session, user: User, book: Book, *, partial: bool = False,
               force: bool = False) -> tuple[AIRecap, bool]:
    """(row, cached) for the whole-book recap of a finished ``book``: the
    same row the volume list shows (``state``), served when fresh, else
    written by ``generate_volume`` (the own-scope row). Raises
    ``recap.RecapNotReady`` when chapters have no digest and not
    ``partial``."""
    st = state(db, user, book)
    if not force and st.row is not None and st.fresh:
        return st.row, True
    cov = st.coverage
    missing = cov["needed"] - cov["digested"]
    if missing and not partial:
        raise recap.RecapNotReady(missing)
    return generate_volume(db, user, book), False


def clear_volume(db: Session, user: User, vol: Book) -> bool:
    """Delete the volume's own-scope row (never the user-scope row of the
    finished-book hook, which "Previously on" shows). True when one was
    deleted. Raises ``AINotConfigured`` without a key."""
    if not can_write(db, user):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    n = len(book_text.book_chapters(vol, db))
    row = _own_row(db, vol, n) if n else None
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
