"""Read again (admin only): delete the stored notes of a book or a series and
read its chapters again.

The notes are shared by every reader (chapter digests, glossary entities and
their notes, recaps), so only an admin may throw them away. Two scopes, like
the digest start:

- ``book``: the open book's chapter digests and recaps. The series' entities
  stay; a chapter read again replaces its own notes as it is written.
- ``series``: the chapter digests and recaps of EVERY active volume of the
  series key (``digest.series_key``), not only the user's scope, plus every
  entity, entity note and reconcile verdict of that key. A digest's
  ``entries_json`` carries entity ids, and SQLite reuses the ids of deleted
  rows (plain INTEGER primary key), so a digest kept for a volume outside
  the scope (an earlier one the admin never marked read, a later one
  another user read) would point at whatever new entity gets the old id.
  The read that follows reads the user's scope (``digest.user_scope``: the
  read earlier volumes plus the open book); the other volumes are read
  again when their readers ask. Refused (``NothingToReread``) when the user
  has no read earlier volume: read the book only instead.

``estimate`` prices the read as if every chapter of the scope were missing
(the same chapters ``companion.start_digest`` reads once the rows are gone,
grouped into units as ``digest.plan_units`` groups them) and counts what
``run`` would delete. ``run`` checks the admin role, a live job touching the
scope (409 ``already_running``), the feature, the key and the spend limits
(409 ``budget``) BEFORE deleting anything, deletes in one transaction,
audits ``ai.reread`` and starts the read with ``companion.start_digest``, with the
merge step (``reconcile``) as the job's follow-up so duplicates are checked when
the read ends.

The delete is committed before the start (a worker's session must see it),
so a start that fails leaves the notes deleted with no job. The pre-checks
are the same ones ``start_digest`` makes, so this is rare (a key removed or
a limit lowered in between); the error then says so, and "Read the missing
chapters" starts the read.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.permissions import is_admin
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.ai_recap import AIRecap
from backend.models.ai_reconcile_verdict import AIReconcileVerdict
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, settings as ai_settings, volume_recap
from backend.services.ai.provider import AIBudgetExceeded, AIError, AINotConfigured
from backend.services.audit import audit

log = logging.getLogger(__name__)

SCOPES = ("book", "series")


class RereadForbidden(AIError):
    """A non-admin asked to read again: 403."""

    status_code = 403
    reason = "admin_only"


class NothingToReread(AIError):
    """The scope has no chapter to read (an unread book with no position,
    ``book`` scope), or a ``series`` re-read without a read earlier volume:
    refused before anything is deleted."""

    status_code = 409
    reason = "nothing_to_read"


FORBIDDEN_MESSAGE = "Only an admin can re-read, the notes are shared by everyone."
NO_EARLIER_MESSAGE = "Mark the earlier volumes read first, or read this book only."
START_FAILED_PREFIX = "The stored notes were deleted but the read could not start"
START_FAILED_SUFFIX = "Use Read the missing chapters to start it."


def _require_admin(user: User) -> None:
    if not is_admin(user):
        raise RereadForbidden(FORBIDDEN_MESSAGE)


# ── the scope ────────────────────────────────────────────────────────────────

def _scope(db: Session, user: User, book: Book, scope: str) -> digest.Scope:
    return digest.Scope(book=book) if scope == "book" else digest.user_scope(db, user, book)


def _series_books(db: Session, book: Book) -> list[Book]:
    """Every active book with ``book``'s series key (``book`` itself always),
    in id order: the volumes whose digests a series re-read deletes."""
    key = digest.series_key(book)
    out = {book.id: book}
    if book.series:
        for b in db.query(Book).filter(Book.status == "active", Book.series.isnot(None)):
            if digest.series_key(b) == key:
                out[b.id] = b
    return [out[i] for i in sorted(out)]


def _all_units(db: Session, sc: digest.Scope, upto: int) -> list[digest.Unit]:
    """``digest.plan_units`` as if no chapter of ``sc`` up to ``upto`` were
    stored: every chapter of the included volumes and the current book's
    chapters before ``upto``, consecutive chapters of one volume grouped up
    to ``CHUNK_CHAPTERS`` / ``CHUNK_WORDS``, a chapter over ``CHUNK_WORDS``
    alone."""
    words_by_book: dict[int, dict[int, int]] = {}
    units: list[digest.Unit] = []
    cur: list[int] = []
    cur_book: int | None = None
    cur_words = 0

    def flush() -> None:
        nonlocal cur, cur_words
        if cur and cur_book is not None:
            units.append(digest.Unit(book_id=cur_book, idxs=tuple(cur), words=cur_words))
        cur, cur_words = [], 0

    for vol, ch in digest.scope_chapters(db, sc, upto):
        if vol.id not in words_by_book:
            words_by_book[vol.id] = book_text.chapter_word_counts(vol, db)
        words = words_by_book[vol.id].get(ch.idx, 0)
        sent = min(words, digest.MAX_CHAPTER_WORDS)
        if words > digest.CHUNK_WORDS:
            flush()
            units.append(digest.Unit(book_id=vol.id, idxs=(ch.idx,), words=sent))
            continue
        if cur and (vol.id != cur_book or ch.idx != cur[-1] + 1 or len(cur) >= digest.CHUNK_CHAPTERS
                    or cur_words + sent > digest.CHUNK_WORDS):
            flush()
        cur_book = vol.id
        cur.append(ch.idx)
        cur_words += sent
    flush()
    return units


def _scope_estimate(db: Session, user: User, sc: digest.Scope, scope: str, upto: int,
                    model: str, hist: dict[str, Any], remaining: float | None) -> dict[str, Any]:
    """The ``estimate.estimate`` shape for every chapter of ``sc`` up to
    ``upto``; ``remaining_budget_usd`` net of what the user's live jobs are
    still expected to spend (``volume_recap.pending_spend``)."""
    from backend.services.ai import estimate

    units = _all_units(db, sc, upto)
    figs = estimate.units_figures(units, model, hist)
    titles = {v.id: v.title for v in sc.volumes()}
    volumes: list[dict[str, Any]] = []
    for u in units:
        if volumes and volumes[-1]["book_id"] == u.book_id:
            volumes[-1]["chapters"] += len(u.idxs)
        else:
            volumes.append({"book_id": u.book_id, "title": titles.get(u.book_id), "chapters": len(u.idxs)})
    left = None
    if remaining is not None:
        left = round(max(0.0, remaining - volume_recap.pending_spend(user.id)), 4)
    return {
        "scope": scope,
        **figs,
        "model": model,
        "remaining_budget_usd": left,
        "over_budget": left is not None and figs["est_cost_usd"] > left + 1e-9,
        "volumes": volumes,
    }


def _delete_ids(db: Session, sc: digest.Scope, series: bool) -> list[int]:
    """The books whose digests and recaps go: the open book, or every book
    of the series key (``_series_books``)."""
    return [b.id for b in _series_books(db, sc.book)] if series else [sc.book.id]


def _deletes(db: Session, sc: digest.Scope, series: bool) -> dict[str, int]:
    """What ``run`` would delete: ``{digests, recaps}`` for ``book``,
    ``{volumes, digests, recaps, entities}`` over the whole series key for
    ``series``."""
    ids = _delete_ids(db, sc, series)
    out: dict[str, int] = {}
    if series:
        out["volumes"] = len(ids)
    out["digests"] = db.query(AIChapterDigest).filter(AIChapterDigest.book_id.in_(ids)).count()
    out["recaps"] = db.query(AIRecap).filter(AIRecap.book_id.in_(ids)).count()
    if series:
        out["entities"] = db.query(AIEntity).filter(
            AIEntity.series_key == digest.series_key(sc.book)).count()
    return out


# ── estimate ─────────────────────────────────────────────────────────────────

def estimate(db: Session, user: User, book: Book, window: companion.Window) -> dict[str, Any]:
    """``{book, series, chapter_idx, chapter_title, deletes: {book: {digests,
    recaps}, series: {volumes, digests, recaps, entities}}}``: both scopes in the
    ``/digest/estimate`` shape, but counting EVERY chapter of the scope up to
    the window (all of them will be missing once the rows are deleted);
    ``series`` is None when the user has no read earlier volume (``run``
    refuses that scope); ``deletes.series`` counts every volume of the
    series key, not only the user's scope. Never calls
    a model. ``RereadForbidden`` for a non-admin."""
    from backend.services.ai import budget, estimate as est_mod

    _require_admin(user)
    ai.check_feature_available(db, digest.FEATURE)
    upto = companion.digest_upto(db, user, book, window)
    model = ai_settings.effective_model(db, digest.FEATURE)
    hist = est_mod.history(db, model)
    remaining = budget.remaining(db, user)
    book_sc = digest.Scope(book=book)
    series_sc = digest.user_scope(db, user, book)
    return {
        "book": _scope_estimate(db, user, book_sc, "book", upto, model, hist, remaining),
        "series": (_scope_estimate(db, user, series_sc, "series", upto, model, hist, remaining)
                   if series_sc.included else None),
        "chapter_idx": window.chapter_idx,
        "chapter_title": window.chapter_title,
        "deletes": {"book": _deletes(db, book_sc, False), "series": _deletes(db, series_sc, True)},
    }


# ── run ──────────────────────────────────────────────────────────────────────

def _job_touches(db: Session, sc: digest.Scope, series: bool) -> bool:
    """A live job (any user) reads a volume the re-read deletes or reads: its
    book is one of them (for ``series`` every book of the series key), or
    it reads the series (its scope is ``series``) and its book shares the
    series key."""
    ids = {v.id for v in sc.volumes()} | set(_delete_ids(db, sc, series))
    key = digest.series_key(sc.book)
    for snap in digest_job.running():
        bid = snap.get("book_id")
        if bid in ids:
            return True
        if series or snap.get("scope") == "series":
            b = db.get(Book, bid)
            if b is not None and digest.series_key(b) == key:
                return True
    return False


def _reset_triggers(book_ids: list[int]) -> None:
    """Forget the position trigger's high-water mark (``digest._last_target``)
    for these books, every user: a reader past chapter k would otherwise not
    re-queue chapters before k that the re-read left missing."""
    wanted = set(book_ids)
    with digest._advance_lock:
        for key in [k for k in digest._last_target if k[1] in wanted]:
            del digest._last_target[key]


def run(db: Session, user: User, book: Book, window: companion.Window,
        scope: str) -> tuple[int, dict[str, Any]]:
    """Delete the stored notes of ``scope`` and start reading it again:
    ``companion.start_digest``'s (status, body), 202 ``{job, estimate,
    coverage, scope, chapter_idx, chapter_title}``. Raises
    ``RereadForbidden`` (403) for a non-admin, ``companion.AlreadyRunning``
    (409 ``already_running``) while a job reads a volume of the scope,
    ``AIDisabled`` / ``AINotConfigured``, ``NothingToReread`` (409) when the
    scope has no chapter to read or a ``series`` scope has no read earlier
    volume, and ``AIBudgetExceeded`` (409 ``budget``) when reading every
    chapter would pass a monthly limit; all before anything is deleted.

    The start itself runs after the delete is committed. Its checks repeat
    the ones above, so it fails only when something changed in between (a
    key removed, a limit lowered, AI switched off); the error keeps its type
    and reason and its message says the notes are gone and to use "Read the
    missing chapters" to start the read."""
    from backend.services.ai import budget

    _require_admin(user)
    if scope not in SCOPES:
        scope = "book"
    series = scope == "series"
    sc = _scope(db, user, book, scope)
    if series and not sc.included:
        raise NothingToReread(NO_EARLIER_MESSAGE)
    if digest_job.active_for(user.id, book.id) is not None or _job_touches(db, sc, series):
        raise companion.AlreadyRunning("A job reading this book or series is already running.")
    ai.check_feature_available(db, digest.FEATURE)
    if ai_settings.resolve_key(db, user) is None:
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")

    full = estimate(db, user, book, window)
    est = full[scope] or full["book"]
    if not est["chapters"]:
        raise NothingToReread("There is nothing to read again: no chapter before your position.")
    pending = volume_recap.pending_spend(user.id)
    err = budget.status(db, user).exceeded_by(est["est_cost_usd"] + pending)
    if err is not None:
        left = max(0.0, err.limit - err.spent - pending)
        raise AIBudgetExceeded(
            f"Reading these chapters again costs about {est['est_cost_usd']:.2f} USD, more than the "
            f"{left:.2f} USD left of the monthly AI budget ({err.limit:.2f} USD). "
            f"Raise it in Settings > AI.",
            which=err.which, spent=err.spent, limit=err.limit)

    ids = _delete_ids(db, sc, series)
    key = digest.series_key(book)
    counts: dict[str, int] = {"volumes": len(ids)} if series else {}
    counts.update(digests=0, recaps=0)
    try:
        counts["digests"] = (db.query(AIChapterDigest).filter(AIChapterDigest.book_id.in_(ids))
                             .delete(synchronize_session=False))
        counts["recaps"] = (db.query(AIRecap).filter(AIRecap.book_id.in_(ids))
                            .delete(synchronize_session=False))
        if series:
            entity_ids = [i for (i,) in db.query(AIEntity.id).filter(AIEntity.series_key == key)]
            counts["notes"] = (db.query(AIEntityNote).filter(AIEntityNote.entity_id.in_(entity_ids))
                               .delete(synchronize_session=False)) if entity_ids else 0
            counts["entities"] = (db.query(AIEntity).filter(AIEntity.series_key == key)
                                  .delete(synchronize_session=False))
            counts["verdicts"] = (db.query(AIReconcileVerdict)
                                  .filter(AIReconcileVerdict.series_key == key)
                                  .delete(synchronize_session=False))
        db.commit()
    except Exception:
        db.rollback()
        raise
    _reset_triggers(ids)
    log.info("reread by user %s of book %s (%s): %s", user.id, book.id, scope, counts)
    audit(db, "ai.reread", user_id=user.id, username=user.username, resource_type="book",
          resource_id=book.id, resource_title=book.title,
          details={"scope": scope, "book_ids": ids, **({"series_key": key} if series else {}),
                   **counts})
    try:
        # A full read ends with the merge step: duplicates the read may
        # still produce are checked without another click.
        return companion.start_digest(db, user, book, window, scope,
                                      followups=(digest_job.FOLLOWUP_RECONCILE,), kind="reread")
    except AIError as e:
        msg = f"{START_FAILED_PREFIX}: {e.message.rstrip()} {START_FAILED_SUFFIX}"
        e.message = msg
        e.args = (msg,)
        raise
    except HTTPException as e:
        if isinstance(e.detail, str):
            e.detail = f"{START_FAILED_PREFIX}: {e.detail.rstrip()} {START_FAILED_SUFFIX}"
        raise
