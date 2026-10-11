"""Recap to here, and "Previously on" (feature ``recap``).

Input, in this order:

1. the chapter SUMMARIES of the reader's scope before the position chapter,
   in reading order, labelled by volume and chapter title. This block carries
   ``cache_control: ephemeral`` so a second recap in the same sitting reuses
   the prefix (system prompt + summaries);
2. a short context block (book, chapter, where the reader is);
3. the text of the position chapter only, cut at the reader's position and
   capped at its last 6000 words, so the recap ends exactly where they are.

Never a glossary entry, never raw text of an earlier chapter, never anything
after the position (see ``companion.Window``).

Cache: ``ai_recaps`` keyed by (book, chapter, scope_hash). A cached row is
served only when the text it saw ends at or before the reader's position and
it was written from as many digests as exist now; otherwise it is rewritten.
``digested`` on the row counts real digests only, not the placeholders of
chapters the model could not read, so ``partial`` stays honest.

"Previously on": when a series volume is marked read, the digest job digests
the whole book and then runs the ``previously_recap`` follow-up registered
below, which writes the recap at ``chapter_idx == chapter count`` (the whole
book, no current-chapter text). The next volume shows it until the reader is
past its chapter 0 (``previously_for``), so the follow-up skips the call when
the user is already past that point in the next volume (or finished it).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.permissions import user_can_see_book
from backend.models.ai_recap import AIRecap
from backend.models.book import Book
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, settings as ai_settings
from backend.services.ai.provider import AIError, AINotConfigured, AIProviderError

logger = logging.getLogger(__name__)

FEATURE = "recap"
MAX_TOKENS = 16_000
MAX_PARAGRAPHS = 6
_EPS = 1e-9

SYSTEM_PROMPT = """\
You write "the story so far" for a reader who is in the middle of a book and \
wants to pick it up again. The reader must not learn anything they have not \
read yet.

You receive chapter summaries of everything before the reader's current \
chapter (earlier volumes of the series first, when there are any), and the \
text of the current chapter up to the exact point where the reader stopped.

Write 2 to 5 short paragraphs:
- Past tense for what has happened. Present tense only for the situation at \
the very end of the supplied text, where the reader is now (for example: \
"Mara has just reached the lighthouse").
- End exactly where the supplied text ends. The last paragraph is about the \
reader's current scene.
- Plain and factual. No judgement of characters or of the book, no \
foreshadowing, no hints, no "little does she know", nothing about what comes \
next, nothing about the book's reputation.
- Use only the supplied summaries and text. Even if you recognise the book, \
never use what you know about it from elsewhere. If something is not in the \
supplied material, it has not happened yet.
- Names as the text writes them. Plain hyphens, never em-dashes.

Return only the JSON the schema describes: the paragraphs in order."""


def build_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
        "required": ["paragraphs"],
        "additionalProperties": False,
    }


class RecapNotReady(AIError):
    """Chapters in the window have no digest yet (and a partial recap was not
    asked for)."""

    status_code = 409

    def __init__(self, missing: int) -> None:
        super().__init__(f"{missing} chapters before this point are not read yet, "
                         "so the recap was not written.")
        self.missing = missing


class NothingToRecap(AIError):
    status_code = 422

    def __init__(self) -> None:
        super().__init__("There is nothing to recap before this page yet.")


@dataclass
class RecapResult:
    row: AIRecap
    cached: bool


# ── cache ────────────────────────────────────────────────────────────────────

def get_cached(db: Session, book_id: int, chapter_idx: int, scope_hash: str) -> AIRecap | None:
    return (
        db.query(AIRecap)
        .filter(AIRecap.book_id == book_id, AIRecap.chapter_idx == chapter_idx,
                AIRecap.scope_hash == scope_hash)
        .first()
    )


def is_fresh(row: AIRecap | None, end_fraction: float, digested: int) -> bool:
    """Servable to a reader whose window ends at ``end_fraction`` with
    ``digested`` digests in scope: the text it saw ends at or before theirs,
    and no digest has been added since it was written."""
    return (row is not None
            and row.position_fraction <= end_fraction + _EPS
            and row.digested >= digested)


def cached_for(db: Session, user: User, book: Book, window: companion.Window,
               scope: digest.Scope | None = None) -> AIRecap | None:
    """The cached recap this user may be served for ``window``, if fresh."""
    scope = scope or digest.user_scope(db, user, book)
    row = get_cached(db, book.id, window.chapter_idx, scope.scope_hash)
    cov = digest.coverage(db, scope, window.chapter_idx)
    return row if is_fresh(row, window.end_fraction, digest.usable(cov)) else None


def clear(db: Session, user: User, book: Book, chapter_idx: int) -> bool:
    """Delete the cache row for (book, chapter, this user's scope) ("Ask
    again"), and the placeholders of chapters before it that the model could
    not read, so the next recap tries those once more. True when a recap row
    was deleted."""
    scope = digest.user_scope(db, user, book)
    digest.clear_failed(db, scope, chapter_idx)
    row = get_cached(db, book.id, chapter_idx, scope.scope_hash)
    if row is not None:
        db.delete(row)
    db.commit()
    return row is not None


# ── prompt ───────────────────────────────────────────────────────────────────

def _volume_label(b: Book) -> str:
    if b.series and b.series_index is not None:
        idx = b.series_index
        num = int(idx) if float(idx).is_integer() else idx
        return f"{b.title} (volume {num} of {b.series})"
    return b.title


def build_user_content(db: Session, book: Book, window: companion.Window, scope: digest.Scope,
                       text: str) -> tuple[list[dict[str, Any]], int]:
    """(content blocks, number of summaries included)."""
    summaries = digest.scoped_digests(db, scope, window.chapter_idx)
    titles = companion._chapter_titles(db, [v.id for v in scope.volumes()])
    vols = {v.id: v for v in scope.volumes()}
    lines: list[str] = []
    current_vol: int | None = None
    used = 0
    for d in summaries:
        summary = (d.summary or "").strip()
        if not summary:
            continue
        if d.book_id != current_vol:
            current_vol = d.book_id
            lines.append(f"\n## {_volume_label(vols[d.book_id])}\n")
        title = titles.get((d.book_id, d.chapter_idx)) or f"Chapter {d.chapter_idx + 1}"
        lines.append(f"### {title}\n{summary}\n")
        used += 1
    summaries_text = ("Chapter summaries of everything before the reader's current chapter, "
                      "in reading order:\n" + "\n".join(lines)) if used else \
        "There are no earlier chapters to summarise."

    n = len(window.chapters)
    finished = window.chapter_idx >= n
    context: dict[str, Any] = {
        "book": book.title,
        "series": book.series,
        "volume": book.series_index,
    }
    if finished:
        context["reader"] = "has finished this book; recap the whole book, ending with its last chapter"
    else:
        context["current_chapter"] = {"number": window.chapter_idx + 1, "of": n,
                                      "title": window.chapter_title}
        context["reader"] = "stopped where the current chapter text below ends"
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": summaries_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Write the recap. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)
                                 + ("" if finished else
                                    "\n\nThe current chapter's text up to the reader's position "
                                    "follows in the next block.")},
    ]
    if not finished:
        blocks.append({"type": "text", "text": text or "(The reader is at the very start of this chapter.)"})
    return blocks, used


def _clean_paragraphs(parsed: dict | None) -> str:
    paras = parsed.get("paragraphs") if isinstance(parsed, dict) else None
    if not isinstance(paras, list):
        return ""
    out = []
    for p in paras[:MAX_PARAGRAPHS]:
        if isinstance(p, str) and p.strip():
            t = " ".join(p.split())
            t = t.replace(" — ", " - ").replace("—", " - ").replace("–", "-")
            out.append(t)
    return "\n\n".join(out)


# ── generate ─────────────────────────────────────────────────────────────────

def generate(db: Session, user: User, book: Book, window: companion.Window, *,
             partial: bool = False, force: bool = False,
             scope: digest.Scope | None = None) -> RecapResult:
    """The recap for ``window`` (cached when fresh, else one ``recap`` call).

    Raises ``RecapNotReady`` when digests are missing and ``partial`` is
    False, ``NothingToRecap`` when there is neither a summary nor text, and
    the typed ``AIError``s from ``run_feature``."""
    scope = scope or digest.user_scope(db, user, book)
    k = window.chapter_idx
    cov = digest.coverage(db, scope, k)
    row = get_cached(db, book.id, k, scope.scope_hash)
    if not force and is_fresh(row, window.end_fraction, digest.usable(cov)):
        return RecapResult(row=row, cached=True)  # type: ignore[arg-type]
    missing = cov["needed"] - cov["digested"]
    if missing and not partial:
        raise RecapNotReady(missing)

    ai.check_feature_available(db, FEATURE)
    text = companion.window_text(book, window, db) if k < len(window.chapters) else ""
    content, used = build_user_content(db, book, window, scope, text)
    if not used and book_text.count_words(text) < digest.MIN_CHAPTER_WORDS:
        raise NothingToRecap()

    result = ai.run_feature(db, user, FEATURE, system=SYSTEM_PROMPT, user_content=content,
                            schema=build_schema(), max_tokens=MAX_TOKENS, book_id=book.id)
    recap_text = _clean_paragraphs(result.parsed)
    if not recap_text:
        raise AIProviderError("The model returned an empty recap.")
    fields = dict(
        position_fraction=window.end_fraction, text=recap_text, model=result.model,
        digested=digest.usable(cov), needed=cov["needed"],
        input_tokens=result.input_tokens + result.cache_read_tokens + result.cache_write_tokens,
        output_tokens=result.output_tokens,
    )
    row = get_cached(db, book.id, k, scope.scope_hash)
    if row is None:
        row = AIRecap(book_id=book.id, chapter_idx=k, scope_hash=scope.scope_hash, **fields)
        db.add(row)
    else:
        for name, value in fields.items():
            setattr(row, name, value)
        row.created_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request stored the same key; serve theirs.
        db.rollback()
        row = get_cached(db, book.id, k, scope.scope_hash)
        if row is None:
            raise
    return RecapResult(row=row, cached=False)


def to_dict(res: RecapResult, window: companion.Window, scope: digest.Scope) -> dict[str, Any]:
    row = res.row
    n = len(window.chapters)
    return {
        "text": row.text,
        "chapter_idx": row.chapter_idx,
        "chapter_title": window.chapter_title if row.chapter_idx < n else None,
        "whole_book": row.chapter_idx >= n,
        "cached": res.cached,
        "partial": row.digested < row.needed,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "scope": scope.to_dict(),
    }


# ── Previously on ────────────────────────────────────────────────────────────

def whole_book_window(db: Session, book: Book) -> companion.Window:
    chapters = book_text.book_chapters(book, db)
    return companion.Window(chapter_idx=len(chapters), until=None, chapters=chapters, position=None)


def next_volume(db: Session, user: User, book: Book) -> Book | None:
    """The volume right after ``book`` in its series that this user can see."""
    if not book.series or book.series_index is None:
        return None
    later = (
        db.query(Book)
        .filter(Book.series == book.series, Book.status == "active", Book.id != book.id,
                Book.series_index.isnot(None), Book.series_index > book.series_index)
        .order_by(Book.series_index.asc(), Book.id.asc())
        .all()
    )
    return next((b for b in later if user_can_see_book(db, user, b)), None)


def _previously_followup(db: Session, user: User, book: Book) -> str | None:
    """Digest-job follow-up: the whole-book recap of a finished volume.

    Skipped (returns why) when the next volume exists and the user is already
    past its chapter 0 or has finished it: "Previously on" only shows at the
    start of the next volume, so that recap would never be read. Written when
    there is no next volume yet (it may arrive later)."""
    if book_text.unavailable_reason(book, db):
        return "This book has no chapter text."
    nxt = next_volume(db, user, book)
    if nxt is not None:
        pos = companion.reader_position(db, user, nxt)
        if pos is not None and (pos.word_pct >= 1.0 or companion.position_chapter(nxt, pos.word_pct, db) > 0):
            return "The next volume is already under way, so this recap would not be shown."
    generate(db, user, book, whole_book_window(db, book), partial=True)
    return None


digest_job.register_followup(digest_job.FOLLOWUP_PREVIOUSLY, _previously_followup)


def previous_volume(db: Session, user: User, book: Book) -> Book | None:
    """The volume right before ``book`` in its series that this user can see."""
    for b in reversed(digest.earlier_volumes(db, book)):
        if user_can_see_book(db, user, b):
            return b
    return None


def previously_for(db: Session, user: User, book: Book) -> tuple[Book, AIRecap] | None:
    """The "Previously on" recap for ``book``: the whole-book recap of the
    previous volume, written for this user's scope of it, when the user marked
    that volume read. None otherwise."""
    prev = previous_volume(db, user, book)
    if prev is None:
        return None
    read = db.query(UserBookStatus.id).filter(
        UserBookStatus.user_id == user.id, UserBookStatus.book_id == prev.id,
        UserBookStatus.status == "read",
    ).first()
    if read is None:
        return None
    n = len(book_text.book_chapters(prev, db))
    if not n:
        return None
    row = get_cached(db, prev.id, n, digest.user_scope(db, user, prev).scope_hash)
    return (prev, row) if row is not None else None


# ── request flow shared by the web and plugin endpoints ─────────────────────

def request_recap(db: Session, user: User, book: Book, window: companion.Window, *,
                  partial: bool = False) -> tuple[int, dict[str, Any]]:
    """(HTTP status, body) for a recap request.

    200 with the recap (cached when fresh, else written now); 202
    ``{needs_digest: true, estimate, coverage, job, chapter_idx,
    chapter_title}`` when chapters in the window have no digest yet and
    ``partial`` is False. Nothing is queued: the caller starts the digests
    explicitly (``POST .../digest``, after asking when there are many) and
    asks again when the job is done, or with ``partial`` after a job that
    could not read every chapter. ``job`` is the active job for the book, if
    any; ``estimate`` is ``{book, series}`` from ``estimate.estimates`` (the
    confirmation dialog shows it; series is None without read earlier
    volumes).
    Raises ``AIDisabled`` (recap or digests off), ``AINotConfigured`` (no
    key) and the other ``AIError``s."""
    ai.check_feature_available(db, FEATURE)
    scope = digest.user_scope(db, user, book)
    row = cached_for(db, user, book, window, scope)
    if row is not None:
        return 200, to_dict(RecapResult(row=row, cached=True), window, scope)
    cov = digest.coverage(db, scope, window.chapter_idx)
    if cov["digested"] < cov["needed"] and not partial:
        ai.check_feature_available(db, digest.FEATURE)
        if ai_settings.resolve_key(db, user) is None:
            raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
        from backend.services.ai import estimate
        return 202, {"needs_digest": True,
                     "estimate": estimate.estimates(db, user, book, window.chapter_idx),
                     "coverage": cov,
                     "job": digest_job.active_for(user.id, book.id),
                     "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title}
    res = generate(db, user, book, window, partial=partial, scope=scope)
    return 200, to_dict(res, window, scope)


def previously_dict(prev: Book, row: AIRecap) -> dict[str, Any]:
    return {
        "book_id": prev.id,
        "title": prev.title,
        "series_index": prev.series_index,
        "text": row.text,
        "chapter_idx": row.chapter_idx,
        "partial": row.digested < row.needed,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }
