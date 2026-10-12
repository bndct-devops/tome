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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.ai_recap import AIRecap
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, recap, settings as ai_settings
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
        "write_all": write_all_state(db, user, book, states),
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
# "Write all" is one digest job (``digest_job``) for the open book, series
# scope, window = the position (``companion.start_window``: chapter 0 when
# the open book has no position), with the follow-ups ``reconcile`` (step
# "merging") and ``volume_recaps`` (step "writing", ``_volume_recaps_followup``).
# It reads the missing chapters of the read earlier volumes (and of the open
# book up to the position), merges duplicate entries, then writes every
# missing or stale volume recap.

UNREAD, FRESH, BUDGET, ERROR = "unread", "fresh", "budget", "error"
WRITE_ALL_FOLLOWUPS = (digest_job.FOLLOWUP_RECONCILE, digest_job.FOLLOWUP_VOLUME_RECAPS)
SUMMARY_WORDS_GUESS = 120   # words of one chapter summary, before any exists


def skip_reason(st: VolumeState) -> str | None:
    """Why the writing step leaves ``st`` alone, or None when it writes it:
    ``fresh`` (the shown row is current), ``unread`` (chapters with no digest
    yet, after the job's reading)."""
    if st.row is not None and st.fresh:
        return FRESH
    cov = st.coverage
    if cov["digested"] < cov["needed"]:
        return UNREAD
    return None


def _needs_write(st: VolumeState) -> bool:
    """The job would write ``st`` once its chapters are read: no row, a
    stale row, or chapters still to read (which makes any row stale)."""
    cov = st.coverage
    return st.row is None or not st.fresh or cov["digested"] < cov["needed"]


def _summary_words(db: Session, vol: Book, n: int) -> int:
    content, _ = build_user_content(db, vol, n)
    return sum(len(str(b.get("text", "")).split()) for b in content)


def _expected_summary_words(db: Session, st: VolumeState) -> int:
    """The words the volume's summaries block will have once every chapter
    is read: the words there now, scaled up for the chapters still missing
    (``SUMMARY_WORDS_GUESS`` per chapter when none exists yet)."""
    have = digest.usable(st.coverage)
    needed = st.coverage["needed"] or st.chapters
    if have <= 0:
        return SUMMARY_WORDS_GUESS * needed
    words = _summary_words(db, st.vol, st.chapters)
    return int(words * max(1.0, needed / have))


def write_all_figures(db: Session, states: list[VolumeState]) -> dict[str, Any]:
    """``{count, est_cost_usd}``: how many volume recaps the "Write all" job
    would write (after its reading) and the sum of one conservative
    ``recap`` call guess per volume (its summaries block, output at
    ``MAX_TOKENS``), rounded to cents."""
    from backend.services.ai import estimate
    model = ai_settings.effective_model(db, FEATURE)
    count, cost = 0, 0.0
    for st in states:
        if not _needs_write(st):
            continue
        count += 1
        cost += estimate.call_cost_guess(FEATURE, model, _expected_summary_words(db, st),
                                         max_tokens=MAX_TOKENS, cached_words=0)
    return {"count": count, "est_cost_usd": round(cost, 2) if count else 0.0}


def _reading_upto(db: Session, user: User, book: Book) -> int:
    """The chapter of the open book a "Write all" job reads up to: always 0.
    "Write all" is about the earlier volumes (the recaps it writes are
    theirs); the open book's own chapters are read by "Recap to here" or the
    position trigger, so the figures shown never count them and the job
    never touches them."""
    return 0


def reading_figures(db: Session, user: User, book: Book) -> dict[str, Any]:
    """``{chapters, volumes, est_cost_usd, est_seconds}``: the reading the
    "Write all" job does first (``volumes`` = how many volumes have chapters
    to read); zeros when nothing is missing."""
    from backend.services.ai import estimate
    est = estimate.estimate(db, user, book, "series", _reading_upto(db, user, book))
    if not est.get("chapters"):
        return {"chapters": 0, "volumes": 0, "est_cost_usd": 0.0, "est_seconds": 0.0}
    return {"chapters": est["chapters"], "volumes": len(est.get("volumes") or []),
            "est_cost_usd": est["est_cost_usd"], "est_seconds": est["est_seconds"]}


MERGE_WORDS_PER_PAIR = 150        # what one pair's context costs in the reconcile call


def merging_figures(db: Session, book: Book) -> dict[str, Any]:
    """``{pairs, calls, est_cost_usd}``: the merging step of "Write all" as
    things stand now (the reading before it may add pairs): the candidate
    pairs ``reconcile`` would send (remembered verdicts left out), its calls
    (``PAIRS_PER_CALL`` pairs each) and one conservative ``reading_digest``
    call guess per call (output at ``reconcile.MAX_TOKENS``)."""
    import math

    from backend.services.ai import estimate, reconcile
    pairs = len(reconcile.candidates(db, book))
    if not pairs:
        return {"pairs": 0, "calls": 0, "est_cost_usd": 0.0}
    calls = math.ceil(pairs / reconcile.PAIRS_PER_CALL)
    model = ai_settings.effective_model(db, digest.FEATURE)
    cost = 0.0
    for i in range(calls):
        n = min(reconcile.PAIRS_PER_CALL, pairs - i * reconcile.PAIRS_PER_CALL)
        cost += estimate.call_cost_guess(digest.FEATURE, model, n * MERGE_WORDS_PER_PAIR,
                                         max_tokens=reconcile.MAX_TOKENS, cached_words=0)
    return {"pairs": pairs, "calls": calls, "est_cost_usd": round(cost, 2)}


def pending_spend(user_id: int) -> float:
    """What the user's live jobs are still expected to spend: their estimate
    less what they have spent, summed (never below zero per job)."""
    return sum(max(0.0, (j.get("est_cost_usd") or 0.0) - (j.get("cost_usd") or 0.0))
               for j in digest_job.running(user_id))


def write_all_state(db: Session, user: User, book: Book,
                    states: list[VolumeState]) -> dict[str, Any]:
    """GET's ``write_all``: ``{count, est_cost_usd, reading: {chapters,
    volumes, est_cost_usd, est_seconds}, merging: {pairs, calls,
    est_cost_usd}, job, last}``. ``job`` is the active
    job for (user, book) (any kind: one at a time per book), ``last`` the
    newest finished job that had the ``volume_recaps`` step; both job
    snapshots (as GET /digest-jobs/{id}) or null. ``remaining_budget_usd``:
    what is left of the tightest monthly limit (``budget.remaining``) less
    what the user's live jobs are still expected to spend
    (``pending_spend``), null without a limit."""
    from backend.services.ai import budget
    left = budget.remaining(db, user)
    if left is not None:
        left = round(max(0.0, left - pending_spend(user.id)), 6)
    return {
        **write_all_figures(db, states),
        "reading": reading_figures(db, user, book),
        "merging": merging_figures(db, book),
        "job": digest_job.active_for(user.id, book.id),
        "last": digest_job.latest_with_followup(db, user.id, book.id,
                                                digest_job.FOLLOWUP_VOLUME_RECAPS),
        "remaining_budget_usd": left,
    }


def _check_budget(db: Session, user: User, cost: float, what: str) -> None:
    from backend.services.ai import budget
    pending = pending_spend(user.id)
    err = budget.status(db, user).exceeded_by(cost + pending)
    if err is not None:
        left = max(0.0, err.limit - err.spent - pending)
        raise AIBudgetExceeded(
            f"{what} costs about {cost:.2f} USD, more than the {left:.2f} USD left of the "
            f"monthly AI budget ({err.limit:.2f} USD). Raise it in Settings > AI.",
            which=err.which, spent=err.spent, limit=err.limit)


def write_all(db: Session, user: User, book: Book) -> dict[str, Any]:
    """POST .../recap/volumes ("Write all"): queue the job and return
    ``{job}`` (202). Raises ``AINotConfigured`` without a key,
    ``AIBudgetExceeded`` (409 ``budget``) when the reading estimate plus the
    recap cost is over what is left of a monthly limit, and
    ``companion.AlreadyRunning`` (409 ``already_running``) while a job for
    the book is active. With nothing to read the job goes straight to
    merging and writing."""
    ai.check_feature_available(db, FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    if not can_write(db, user):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    if digest_job.active_for(user.id, book.id) is not None:
        raise companion.AlreadyRunning("A job for this book is already running.")
    states = [state(db, user, v) for v in included_volumes(db, user, book)]
    figs = write_all_figures(db, states)
    reading = reading_figures(db, user, book)
    merging = merging_figures(db, book)
    total = figs["est_cost_usd"] + reading["est_cost_usd"] + merging["est_cost_usd"]
    if total > 0:
        _check_budget(db, user, total, "Reading the chapters and writing the recaps")
    job = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=_reading_upto(db, user, book),
                             followups=WRITE_ALL_FOLLOWUPS, current_book_only=False)
    if not job.get("total") and reading["chapters"]:
        job = {**job, "total": reading["chapters"], "est_chapters": reading["chapters"],
               "est_cost_usd": reading["est_cost_usd"], "est_seconds": reading["est_seconds"]}
    return {"job": job}


def start_reconcile(db: Session, user: User, book: Book) -> dict[str, Any]:
    """POST .../reconcile ("Merge duplicates"): a digest job that reads
    nothing (``upto`` 0, scope book), step merging only; ``{job}`` (202).
    409 ``already_running`` while a job for the book is active."""
    ai.check_feature_available(db, digest.FEATURE)
    if not can_write(db, user):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    if digest_job.active_for(user.id, book.id) is not None:
        raise companion.AlreadyRunning("A job for this book is already running.")
    job = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=0,
                             followups=(digest_job.FOLLOWUP_RECONCILE,), current_book_only=True)
    return {"job": job}


def _skip(st: VolumeState, reason: str, message: str | None = None) -> dict[str, Any]:
    cov = st.coverage
    out: dict[str, Any] = {"book_id": st.vol.id, "title": st.vol.title,
                           "series_index": st.vol.series_index, "reason": reason,
                           "missing_chapters": max(0, cov["needed"] - cov["digested"])}
    if message:
        out["message"] = message
    return out


def _volume_recaps_followup(db: Session, user: User, book: Book) -> dict[str, Any]:
    """Step "writing" of a "Write all" job: one ``recap`` call each (under
    the per-volume in-flight guard) for every included earlier volume whose
    shown row is missing or stale and that has no undigested chapter;
    ``step_done``/``step_total`` count the recaps. A cancel stops after the
    current call. ``AIBudgetExceeded`` or a missing / invalid / disabled key
    stops the step (re-raised: the job ends ``stopped`` with the reason;
    what was written stays). Any other failure skips that volume. Returns
    ``{written: [book_id], failed: [{book_id, title, series_index, reason,
    message}], skipped: [...]}`` for the job's follow-up result."""
    states = [state(db, user, v) for v in included_volumes(db, user, book)]
    targets = [st for st in states if skip_reason(st) is None]
    skipped = [_skip(st, r) for st in states if (r := skip_reason(st)) is not None]
    written: list[int] = []
    failed: list[dict[str, Any]] = []
    digest_job.step_progress(0, len(targets))
    for i, st in enumerate(targets):
        if digest_job.cancel_requested():
            break
        try:
            with companion.in_flight("volume_recap", user.id, st.vol.id,
                                     "This recap is already being written."):
                generate_volume(db, user, st.vol)
        except (AIBudgetExceeded, AINotConfigured, AIKeyInvalid, AIDisabled) as exc:
            db.rollback()
            exc.followup_result = {"written": written, "failed": failed, "skipped": skipped}  # type: ignore[attr-defined]
            raise
        except AIError as exc:   # incl. companion.AlreadyRunning, NothingToRecap
            db.rollback()
            failed.append(_skip(st, ERROR, getattr(exc, "message", None) or str(exc)))
        else:
            written.append(st.vol.id)
        digest_job.step_progress(i + 1)
    return {"written": written, "failed": failed, "skipped": skipped}


digest_job.register_followup(digest_job.FOLLOWUP_VOLUME_RECAPS, _volume_recaps_followup)


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
