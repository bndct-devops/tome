"""Per-chapter digests: the mechanism under recap, glossary and Recall.

A digest is the model's reading of ONE chapter: a short summary (for the
recap roll-up) and the glossary delta of that chapter (names, places, terms,
each with one line, a fuller ``more`` and a verbatim ``evidence`` quote).
Digests are user-independent and stored once per (book, chapter) in
``ai_chapter_digests``; what a user sees is a fold over the digests in their
scope.

Spoiler safety is structural:

- ``digest_chapter`` sends the model chapter k's text and the glossary state
  folded from the chapters BEFORE k in reading order, nothing else.
- The triggers only ever ask for chapters the reader has finished: a reader
  in chapter k (``book_text.chapter_for_fraction`` of the position as a word
  fraction, ``book_text.reading_fraction``) gets digests of chapters
  ``0..k-1`` of the current book (``upto=k``, exclusive); the on-demand recap
  and glossary add every chapter of the earlier volumes they marked read. A
  finished book gets all of its chapters.
- Every entry's ``evidence`` must be a quote of at least three words that
  occurs in the chapter text it came from, on word boundaries (whitespace,
  case and quote style normalised), and that names the entry; otherwise the
  entry is dropped before storage (``check_entries``).

Reading order across a series: earlier volumes (same ``series``, lower
``series_index``) in index order, each chapter in ``idx`` order, then the
current book. Which earlier volumes count is per user (``user_scope``): only
those with ``UserBookStatus.status == "read"``; the rest are listed as
excluded.

Triggers: ``on_position_advanced`` (called by the plugin position PUT and the
web status PUT; the current book's finished chapters only) and a book
becoming "read" through any write path (an ORM flush hook on
``UserBookStatus.status``, enqueued after the commit; capped per write and
per user per day), which digests the whole book and then runs the
``previously_recap`` follow-up the recap module registers with
``digest_job.register_followup``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.permissions import user_can_see_book
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.book import Book, BookChapter
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import settings as ai_settings
from backend.services.ai.provider import AIDisabled, AIError, AIKeyInvalid, AINotConfigured

logger = logging.getLogger(__name__)

FEATURE = "reading_digest"
RECAP_FEATURE = "recap"

KINDS = ("person", "place", "term")
MAX_EVIDENCE_WORDS = 20
MIN_EVIDENCE_WORDS = 3
MAX_CHAPTER_WORDS = 30_000     # a longer chapter is digested from its first 30k words
MIN_CHAPTER_WORDS = 20         # below this: stored as an empty digest, no model call
GLOSSARY_STATE_MAX = 400       # prior entries shown to the model (most recently seen)
MAX_ENTRIES_PER_CHAPTER = 60
MAX_TOKENS = 16_000
NO_MODEL = "none"              # AIChapterDigest.model for a chapter too short to digest
FAILED_MODEL = "failed"        # a billed answer Tome could not use (refusal, cut off): not re-sent

_NAME_MAX = 120
_LINE_MAX = 300
_MORE_MAX = 1200
_SUMMARY_MAX = 1500

SYSTEM_PROMPT = """\
You read one chapter of a book for a spoiler-safe reading companion. Your \
output feeds a recap and a glossary that a reader opens in the middle of the \
book, so it must contain nothing beyond the chapter you are given.

You receive the book's title, the chapter's position and title, the glossary \
so far (names, places and terms from the chapters before this one, each with \
its current one-line description), and the full text of this chapter.

Return:
- summary: 2 to 5 sentences on what happens in this chapter, in the past \
tense. Plain and factual: no judgement, no foreshadowing, no hints about what \
comes later, nothing about the book's reputation.
- entries: the people, places and terms (kind: person, place or term) that \
this chapter introduces, plus glossary entries whose description changes \
because of this chapter. For an entry already in the glossary, repeat its \
name exactly as written there. Skip entries that appear but do not change. \
For each entry:
  - line: one sentence, at most 25 words, saying who or what it is as of the \
end of this chapter.
  - more: one to three sentences of fuller context from this chapter and the \
glossary so far.
  - evidence: a short quote, at most 20 words, copied character for character \
from this chapter's text, that mentions the entry. Never paraphrase it.

Rules:
- Use only the supplied text and glossary. Even if you recognise the book, \
never use what you know about it from elsewhere, and never mention anything \
that has not happened by the end of this chapter.
- Names as the text writes them. A narrator speaking as "I" is an entry only \
when the text gives a name.
- No entry for the author, the book itself, or ordinary words.
- Front or back matter (title page, contents, a note about the author, \
afterword, publisher's notes): return an empty summary and no entries.
- Return only the JSON the schema describes."""


def build_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "line": {"type": "string"},
                        "more": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["name", "kind", "line", "more", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "entries"],
        "additionalProperties": False,
    }


class DigestUnavailable(Exception):
    """The chapter cannot be digested: not an EPUB on disk, no chapter map,
    or no such chapter."""


# ── normalisation + the citation check ───────────────────────────────────────

_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", " ": " ",
})
_EDGE_PUNCT = " \t\n\"'.,;:!?-()[]{}*_"


def normalize_name(name: str) -> str:
    """Glossary key: whitespace collapsed, case folded."""
    return " ".join((name or "").split()).casefold()


def normalize_text(text: str) -> str:
    """For the citation check: case folded, typographic quotes/dashes/ellipsis
    mapped to ASCII, whitespace collapsed."""
    return " ".join((text or "").translate(_QUOTE_MAP).casefold().split())


def _clean_evidence(evidence: str) -> str:
    ev = " ".join((evidence or "").split())
    ev = ev.strip(_EDGE_PUNCT + "‘’“”…")
    return book_text.truncate_words(ev, MAX_EVIDENCE_WORDS).strip(_EDGE_PUNCT + "‘’“”…")


# Scripts written without spaces: no word boundary between their characters.
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7a3]")
# Name parts too common to tie a quote to an entry on their own.
_NAME_STOPWORDS = frozenset({
    "the", "and", "for", "mrs", "miss", "sir", "lady", "lord", "king", "queen", "von", "van",
    "der", "den", "del", "les", "los", "las", "das", "die", "old", "young", "little", "great",
})


def _find_word(needle: str, haystack: str) -> bool:
    """``needle`` occurs in ``haystack`` (both normalised) as whole words."""
    if not needle:
        return False
    left = "" if _CJK_RE.match(needle[0]) else r"(?<!\w)"
    right = "" if _CJK_RE.match(needle[-1]) else r"(?!\w)"
    return re.search(left + re.escape(needle) + right, haystack) is not None


def evidence_in_text(evidence: str, normalized_chapter: str) -> bool:
    """The quote is at least three words and occurs in the chapter on word
    boundaries (a fragment like "in d" never passes)."""
    ev = normalize_text(evidence)
    if not ev or book_text.count_words(ev) < MIN_EVIDENCE_WORDS:
        return False
    return _find_word(ev, normalized_chapter)


def names_entry(name: str, evidence: str) -> bool:
    """The quote names the entry: the whole name, or one of its parts of three
    or more letters that is not a title or particle, as a whole word."""
    n = normalize_text(name)
    ev = normalize_text(evidence)
    if not n or not ev:
        return False
    if _find_word(n, ev):
        return True
    return any(len(tok) >= 3 and tok not in _NAME_STOPWORDS and _find_word(tok, ev)
               for tok in book_text._WORD_RE.findall(n))


def _str(v: Any, limit: int) -> str:
    return " ".join(v.split())[:limit] if isinstance(v, str) else ""


def check_entries(raw_entries: Any, chapter_text: str) -> list[dict[str, str]]:
    """Validate the model's entries against the chapter text it was given.

    Kept: a non-empty name, a known kind, a line, and an ``evidence`` quote of
    at least three words that occurs in ``chapter_text`` on word boundaries
    (normalised) and names the entry (``names_entry``), so a real sentence
    about someone else cannot carry an invented entry. Evidence longer than
    20 words is cut to its first 20 words (still a verbatim slice). One entry
    per name. Everything else is dropped."""
    if not isinstance(raw_entries, list):
        return []
    haystack = normalize_text(chapter_text)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        name = _str(raw.get("name"), _NAME_MAX).strip(_EDGE_PUNCT)
        key = normalize_name(name)
        if not key or key in seen:
            continue
        kind = raw.get("kind") if raw.get("kind") in KINDS else None
        line = _str(raw.get("line"), _LINE_MAX)
        if kind is None or not line:
            continue
        evidence = _clean_evidence(raw.get("evidence") if isinstance(raw.get("evidence"), str) else "")
        if not evidence_in_text(evidence, haystack) or not names_entry(name, evidence):
            continue
        seen.add(key)
        out.append({
            "name": name, "kind": kind, "line": line,
            "more": _str(raw.get("more"), _MORE_MAX), "evidence": evidence,
        })
        if len(out) >= MAX_ENTRIES_PER_CHAPTER:
            break
    return out


# ── the fold ─────────────────────────────────────────────────────────────────

@dataclass
class Entry:
    name: str
    kind: str
    line: str
    more: str
    evidence: str
    first_seen: tuple[int, int]   # (book_id, chapter_idx)
    last_seen: tuple[int, int]    # where line/more/evidence come from

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "line": self.line, "more": self.more,
            "evidence": self.evidence,
            "first_seen": {"book_id": self.first_seen[0], "chapter_idx": self.first_seen[1]},
            "last_seen": {"book_id": self.last_seen[0], "chapter_idx": self.last_seen[1]},
        }


def digest_entries(d: Any) -> list[dict[str, Any]]:
    raw = getattr(d, "entries_json", None)
    if raw is None:
        return list(getattr(d, "entries", []) or [])
    try:
        val = json.loads(raw or "[]")
    except ValueError:
        return []
    return val if isinstance(val, list) else []


def fold_entries(digests: Iterable[Any]) -> list[Entry]:
    """Glossary state after the given digests, which must be in reading order.

    Entries merge by normalised name (case, whitespace). A later chapter's
    ``line``/``more``/``evidence`` replaces the earlier one (and its ``kind``);
    ``first_seen`` and the first spelling of the name are kept. Result is in
    order of first appearance. Pure and deterministic."""
    by_key: dict[str, Entry] = {}
    for d in digests:
        where = (int(d.book_id), int(d.chapter_idx))
        for e in digest_entries(d):
            if not isinstance(e, dict):
                continue
            key = normalize_name(e.get("name", ""))
            if not key:
                continue
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = Entry(
                    name=" ".join(str(e.get("name", "")).split()),
                    kind=e.get("kind") if e.get("kind") in KINDS else "term",
                    line=e.get("line") or "", more=e.get("more") or "",
                    evidence=e.get("evidence") or "", first_seen=where, last_seen=where,
                )
                continue
            if e.get("line"):
                cur.line = e["line"]
                cur.more = e.get("more") or ""
                cur.evidence = e.get("evidence") or cur.evidence
                cur.last_seen = where
                if e.get("kind") in KINDS:
                    cur.kind = e["kind"]
    return list(by_key.values())


# ── reading order + scope ────────────────────────────────────────────────────

def earlier_volumes(db: Session, book: Book) -> list[Book]:
    """Active books of the same series with a lower ``series_index``, in order."""
    if not book.series or book.series_index is None:
        return []
    return (
        db.query(Book)
        .filter(
            Book.series == book.series,
            Book.status == "active",
            Book.id != book.id,
            Book.series_index.isnot(None),
            Book.series_index < book.series_index,
        )
        .order_by(Book.series_index.asc(), Book.id.asc())
        .all()
    )


def reading_order(db: Session, book: Book) -> list[Book]:
    """The series' reading order up to and including ``book`` (the same for
    every user): earlier volumes by ``series_index``, then ``book``."""
    return earlier_volumes(db, book) + [book]


@dataclass
class Scope:
    book: Book
    included: list[Book] = field(default_factory=list)            # earlier volumes, read
    excluded: list[tuple[Book, str]] = field(default_factory=list)  # (volume, reason)

    def volumes(self) -> list[Book]:
        return self.included + [self.book]

    @property
    def scope_hash(self) -> str:
        """sha256 of the sorted included earlier-volume ids (cache key part)."""
        ids = ",".join(str(i) for i in sorted(b.id for b in self.included))
        return hashlib.sha256(ids.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "included_volumes": [
                {"book_id": b.id, "title": b.title, "series_index": b.series_index}
                for b in self.included
            ],
            "excluded_volumes": [
                {"book_id": b.id, "title": b.title, "series_index": b.series_index, "reason": reason}
                for b, reason in self.excluded
            ],
            "scope_hash": self.scope_hash,
        }


def user_scope(db: Session, user: User, book: Book) -> Scope:
    """Which earlier volumes count for ``user``: those they marked read.

    Excluded volumes carry a reason: ``not_read``, or ``no_chapters`` /
    ``not_epub`` for a read volume that has no chapter text to digest.
    Volumes the user cannot see are left out silently (listing them would
    leak their titles)."""
    scope = Scope(book=book)
    earlier = [b for b in earlier_volumes(db, book) if user_can_see_book(db, user, b)]
    if not earlier:
        return scope
    read_ids = {
        bid for (bid,) in db.query(UserBookStatus.book_id).filter(
            UserBookStatus.user_id == user.id,
            UserBookStatus.book_id.in_([b.id for b in earlier]),
            UserBookStatus.status == "read",
        )
    }
    for b in earlier:
        if b.id not in read_ids:
            scope.excluded.append((b, "not_read"))
            continue
        reason = book_text.unavailable_reason(b, db)
        if reason:
            scope.excluded.append((b, reason))
        else:
            scope.included.append(b)
    return scope


def _chapter_lists(db: Session, books: list[Book]) -> dict[int, list[BookChapter]]:
    if not books:
        return {}
    rows = (
        db.query(BookChapter)
        .filter(BookChapter.book_id.in_([b.id for b in books]))
        .order_by(BookChapter.book_id, BookChapter.idx)
        .all()
    )
    out: dict[int, list[BookChapter]] = {b.id: [] for b in books}
    for r in rows:
        out[r.book_id].append(r)
    return out


def _digests_by_book(db: Session, book_ids: list[int]) -> dict[int, dict[int, AIChapterDigest]]:
    out: dict[int, dict[int, AIChapterDigest]] = {bid: {} for bid in book_ids}
    if not book_ids:
        return out
    for d in db.query(AIChapterDigest).filter(AIChapterDigest.book_id.in_(book_ids)):
        out[d.book_id][d.chapter_idx] = d
    return out


def scope_chapters(db: Session, scope: Scope, upto: int) -> list[tuple[Book, BookChapter]]:
    """(volume, chapter) pairs the fold covers, in reading order: every
    chapter of the included earlier volumes, then the current book's chapters
    with ``idx < upto``."""
    vols = scope.volumes()
    chapters = _chapter_lists(db, vols)
    out: list[tuple[Book, BookChapter]] = []
    for b in scope.included:
        out.extend((b, c) for c in chapters.get(b.id, []))
    out.extend((scope.book, c) for c in chapters.get(scope.book.id, []) if c.idx < upto)
    return out


def scoped_digests(db: Session, scope: Scope, upto: int) -> list[AIChapterDigest]:
    """The stored digests the user's fold runs over, in reading order."""
    pairs = scope_chapters(db, scope, upto)
    have = _digests_by_book(db, [b.id for b in scope.volumes()])
    return [have[b.id][c.idx] for b, c in pairs if c.idx in have.get(b.id, {})]


def missing_chapters(db: Session, scope: Scope, upto: int) -> list[tuple[Book, BookChapter]]:
    """Pairs in the scope that have no digest yet, in reading order."""
    pairs = scope_chapters(db, scope, upto)
    have = _digests_by_book(db, [b.id for b in scope.volumes()])
    return [(b, c) for b, c in pairs if c.idx not in have.get(b.id, {})]


def coverage(db: Session, scope: Scope, upto: int) -> dict[str, int]:
    """``digested`` counts every stored row (a ``FAILED_MODEL`` placeholder
    too: it is done and never re-sent); ``failed`` is how many of those are
    placeholders, so ``digested - failed`` chapters actually feed the fold."""
    pairs = scope_chapters(db, scope, upto)
    have = _digests_by_book(db, [b.id for b in scope.volumes()])
    rows = [have[b.id][c.idx] for b, c in pairs if c.idx in have.get(b.id, {})]
    return {"digested": len(rows), "needed": len(pairs),
            "failed": sum(1 for d in rows if d.model == FAILED_MODEL)}


def usable(cov: dict[str, int]) -> int:
    """Chapters in a coverage that have a real digest (not a failed placeholder)."""
    return cov["digested"] - cov.get("failed", 0)


def clear_failed(db: Session, scope: Scope, upto: int) -> int:
    """Delete the ``FAILED_MODEL`` placeholders in the window so the next job
    tries those chapters once more (an explicit "Ask again", never a poll)."""
    pairs = scope_chapters(db, scope, upto)
    have = _digests_by_book(db, [b.id for b in scope.volumes()])
    rows = [have[b.id][c.idx] for b, c in pairs
            if c.idx in have.get(b.id, {}) and have[b.id][c.idx].model == FAILED_MODEL]
    for d in rows:
        db.delete(d)
    return len(rows)


def glossary_state(db: Session, user: User, book: Book, upto: int,
                   scope: Scope | None = None) -> list[Entry]:
    """The user's glossary: the fold over their scope up to (not including)
    chapter ``upto`` of ``book``."""
    scope = scope or user_scope(db, user, book)
    return fold_entries(scoped_digests(db, scope, upto))


def _prior_digests(db: Session, book: Book, idx: int) -> list[AIChapterDigest]:
    """Digests strictly before (book, idx) in the series' reading order, the
    same for every user: every stored digest of the earlier volumes, then
    this book's chapters before ``idx``. No user's read status narrows it, so
    a stored digest does not depend on who happened to trigger it (it can
    still depend on which earlier chapters had been digested by then)."""
    vols = reading_order(db, book)
    have = _digests_by_book(db, [v.id for v in vols])
    out: list[AIChapterDigest] = []
    for v in vols:
        rows = sorted(have.get(v.id, {}).values(), key=lambda d: d.chapter_idx)
        if v.id == book.id:
            out.extend(d for d in rows if d.chapter_idx < idx)
            break
        out.extend(rows)
    return out


# ── digesting one chapter ────────────────────────────────────────────────────

def get_digest(db: Session, book_id: int, idx: int) -> AIChapterDigest | None:
    return (
        db.query(AIChapterDigest)
        .filter(AIChapterDigest.book_id == book_id, AIChapterDigest.chapter_idx == idx)
        .first()
    )


def build_user_content(book: Book, chapter: BookChapter, chapter_count: int,
                       prior: list[Entry], text: str) -> list[dict[str, Any]]:
    """Two blocks: the context (book, chapter, glossary so far) and the chapter
    text on its own, verbatim."""
    recent = sorted(prior, key=lambda e: e.last_seen)[-GLOSSARY_STATE_MAX:]
    recent_keys = {e.key for e in recent}
    state = [{"name": e.name, "kind": e.kind, "line": e.line} for e in prior if e.key in recent_keys]
    context = {
        "book": book.title,
        "series": book.series,
        "volume": book.series_index,
        "chapter": {"number": chapter.idx + 1, "of": chapter_count, "title": chapter.title},
        "glossary_so_far": state,
    }
    return [
        {"type": "text", "text": "Digest this chapter. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)
                                 + "\n\nThe chapter text follows in the next block."},
        {"type": "text", "text": text},
    ]


def _store(db: Session, row: AIChapterDigest) -> AIChapterDigest:
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Another job stored this chapter between our check and the insert.
        db.rollback()
        existing = get_digest(db, row.book_id, row.chapter_idx)
        if existing is None:
            raise
        return existing
    return row


def digest_chapter(db: Session, user: User, book: Book, idx: int) -> AIChapterDigest:
    """Digest chapter ``idx`` of ``book`` (or return the stored digest).

    The model receives this chapter's text and the glossary folded from the
    digests before it in the series' reading order (``_prior_digests``, not
    ``user``'s scope: a digest is shared by every reader). Goes through
    ``ai.run_feature`` with feature ``reading_digest``, which records the
    usage row. Raises ``DigestUnavailable`` and the typed ``AIError``s.

    A billed answer Tome cannot use (a refusal, an answer cut off or
    unreadable) is the same on every retry, so it is stored as a
    ``FAILED_MODEL`` placeholder (no summary, no entries) before the error is
    re-raised: the chapter is never re-sent by a poll or a trigger, and
    coverage reports it as ``failed``. "Ask again" on the recap clears it.
    Errors without a billed answer (outage, rate limit, no key) store
    nothing and stay retryable."""
    existing = get_digest(db, book.id, idx)
    if existing is not None:
        return existing
    chapters = book_text.book_chapters(book, db)
    chapter = next((c for c in chapters if c.idx == idx), None)
    if chapter is None:
        raise DigestUnavailable(f"Book {book.id} has no chapter {idx}.")
    text = book_text.chapter_text(book, idx, db)
    if text is None:
        raise DigestUnavailable(f"No text for chapter {idx} of book {book.id}.")

    if book_text.count_words(text) < MIN_CHAPTER_WORDS:
        return _store(db, AIChapterDigest(book_id=book.id, chapter_idx=idx, model=NO_MODEL,
                                          summary="", entries_json="[]"))

    sent = book_text.truncate_words(text, MAX_CHAPTER_WORDS)
    prior = fold_entries(_prior_digests(db, book, idx))
    try:
        result = ai.run_feature(
            db, user, FEATURE,
            system=SYSTEM_PROMPT,
            user_content=build_user_content(book, chapter, len(chapters), prior, sent),
            schema=build_schema(),
            max_tokens=MAX_TOKENS,
        )
    except AIError as exc:
        billed = getattr(exc, "result", None)
        if billed is not None and not isinstance(exc, (AIDisabled, AINotConfigured, AIKeyInvalid)):
            # run_feature has already committed the usage row of this answer.
            _store(db, AIChapterDigest(
                book_id=book.id, chapter_idx=idx, model=FAILED_MODEL, summary="", entries_json="[]",
                input_tokens=billed.input_tokens + billed.cache_read_tokens + billed.cache_write_tokens,
                output_tokens=billed.output_tokens,
            ))
        raise
    parsed = result.parsed or {}
    summary = _str(parsed.get("summary"), _SUMMARY_MAX)
    entries = check_entries(parsed.get("entries"), sent)
    return _store(db, AIChapterDigest(
        book_id=book.id, chapter_idx=idx, model=result.model, summary=summary,
        entries_json=json.dumps(entries, ensure_ascii=False),
        input_tokens=result.input_tokens + result.cache_read_tokens + result.cache_write_tokens,
        output_tokens=result.output_tokens,
    ))


# ── triggers ─────────────────────────────────────────────────────────────────

# (user_id, book_id) -> highest position chapter already acted on. In memory,
# single process (like the job): a restart costs at most one extra enqueue.
_advance_lock = threading.Lock()
_last_target: dict[tuple[int, int], int] = {}

# Finished-book jobs (whole-book digest + an Opus "Previously on") are capped:
# a reading-CSV import, a plugin sweep or a sync-code batch can mark hundreds
# of books read in one go, and none of that should turn into hundreds of
# unasked-for calls. The rest are skipped (logged); the on-demand recap and
# glossary still work for them.
MAX_FINISHED_PER_COMMIT = 3
MAX_FINISHED_PER_USER_DAY = 5
_DAY = 86_400.0
_finished_log: dict[int, list[tuple[float, int]]] = {}   # user_id -> [(time, book_id)]


def reset_triggers() -> None:
    """Tests only."""
    with _advance_lock:
        _last_target.clear()
        _finished_log.clear()


def _finished_allowed(user_id: int, book_id: int, now: float | None = None) -> bool:
    """Count a finished-book job against the user's daily cap; False when the
    cap is reached (the same book again within the day is always allowed:
    it coalesces into the job already queued)."""
    now = time.time() if now is None else now
    with _advance_lock:
        recent = [(t, b) for t, b in _finished_log.get(user_id, []) if now - t < _DAY]
        if any(b == book_id for _, b in recent):
            _finished_log[user_id] = recent
            return True
        if len(recent) >= MAX_FINISHED_PER_USER_DAY:
            _finished_log[user_id] = recent
            return False
        recent.append((now, book_id))
        _finished_log[user_id] = recent
        return True


def _user_can_digest(db: Session, user: User, *features: str) -> bool:
    if not ai_settings.ai_enabled(db):
        return False
    if not all(ai_settings.feature_enabled(db, f) for f in features):
        return False
    return ai_settings.resolve_key(db, user) is not None


def on_position_advanced(db: Session, user: User, book: Book, pct: float | None,
                         locator: str | None = None) -> dict | None:
    """Enqueue digests of the chapters the reader has just finished.

    Called after the position is committed (plugin ``PUT
    /tome-sync/position/{id}`` with its xpointer, web ``PUT /books/{id}/status``
    with its CFI). ``pct`` is a page or section fraction, so the chapter comes
    from ``book_text.reading_fraction`` (the locator, else a margin), never
    from ``pct`` itself. Acts only when that chapter k (rounded down) is past
    chapter 0 and past the highest chapter already acted on for this user and
    book, the book is an EPUB with a chapter map, AI and ``reading_digest``
    are on, and the user's key resolves. Enqueues chapters ``< k`` of THIS
    book only, if any is missing; earlier volumes are left to the on-demand
    recap and glossary (which ask before spending on a whole series). Never
    raises; returns the job snapshot or None."""
    try:
        if pct is None or not ai_settings.env_enabled():
            return None
        if book_text.unavailable_reason(book, db):
            return None
        # Gate before reading the book file to place the reader.
        if not _user_can_digest(db, user, FEATURE):
            return None
        k = book_text.chapter_for_fraction(book, book_text.reading_fraction(book, pct, locator, db), db)
        if not k:
            return None
        key = (user.id, book.id)
        with _advance_lock:
            if k <= _last_target.get(key, 0):
                return None
            _last_target[key] = k
        if not missing_chapters(db, Scope(book=book), k):
            return None
        from backend.services.ai import digest_job
        return digest_job.enqueue(user_id=user.id, book_id=book.id, upto=k, current_book_only=True)
    except Exception:  # noqa: BLE001 - never break a position sync
        logger.exception("digest trigger failed for user %s book %s", user.id, book.id)
        return None


def finished_book_job(db: Session, user: User, book: Book) -> tuple[int, int, int] | None:
    """(user_id, book_id, chapter_count) when a finished ``book`` should get its
    whole-book digest and the "Previously on" recap, else None: the book is
    in a series, has chapter text, AI plus ``reading_digest`` and ``recap``
    are on, and the user's key resolves."""
    if not ai_settings.env_enabled() or not book.series:
        return None
    if book_text.unavailable_reason(book, db):
        return None
    if not _user_can_digest(db, user, FEATURE, RECAP_FEATURE):
        return None
    n = len(book_text.book_chapters(book, db))
    return (user.id, book.id, n) if n else None


def on_book_finished(db: Session, user: User, book: Book) -> dict | None:
    """Enqueue the whole-book digest + ``previously_recap`` follow-up now.
    The ORM hook below calls the same gate and enqueues after commit; this
    direct form is for callers outside a write transaction."""
    job = finished_book_job(db, user, book)
    if job is None or not _finished_allowed(user.id, book.id):
        return None
    from backend.services.ai import digest_job
    return digest_job.enqueue(user_id=job[0], book_id=job[1], upto=job[2],
                              followups=(digest_job.FOLLOWUP_PREVIOUSLY,))


# A book becoming "read" happens on many write paths (web status PUT, the
# shared sticky-completion rule for device positions, sessions and manual
# logs, the sidecar sweep, a position restore). Rather than a call at each,
# one flush hook notices a UserBookStatus.status change to "read", gates it
# while the transaction is still open, and enqueues after the commit (never
# for a rolled-back write).

_FINISHED_KEY = "ai_digest_finished"


@event.listens_for(Session, "after_flush")
def _collect_finished_books(session: Session, _flush_context: Any) -> None:
    if not ai_settings.env_enabled():
        return
    try:
        candidates: list[tuple[int, int]] = []
        for obj in list(session.new) + list(session.dirty):
            if not isinstance(obj, UserBookStatus):
                continue
            hist = sa_inspect(obj).attrs.status.history
            if "read" not in (hist.added or ()):
                continue
            if hist.deleted and hist.deleted[0] == "read":
                continue
            candidates.append((obj.user_id, obj.book_id))
        if not candidates:
            return
        ready = session.info.setdefault(_FINISHED_KEY, {})
        for user_id, book_id in candidates:
            if (user_id, book_id) in ready:
                continue
            if len(ready) >= MAX_FINISHED_PER_COMMIT:
                logger.info("finished-book digest skipped for user %s book %s: more than %d "
                            "books marked read in one write", user_id, book_id,
                            MAX_FINISHED_PER_COMMIT)
                continue
            user = session.get(User, user_id)
            book = session.get(Book, book_id)
            if user is None or book is None:
                continue
            job = finished_book_job(session, user, book)
            if job is not None:
                ready[(user_id, book_id)] = job
    except Exception:  # noqa: BLE001 - never break the write that finished the book
        logger.exception("finished-book digest hook failed")


@event.listens_for(Session, "after_commit")
def _enqueue_finished_books(session: Session) -> None:
    ready = session.info.pop(_FINISHED_KEY, None)
    if not ready:
        return
    try:
        from backend.services.ai import digest_job
        for user_id, book_id, n in ready.values():
            if not _finished_allowed(user_id, book_id):
                logger.info("finished-book digest skipped for user %s book %s: daily cap of %d",
                            user_id, book_id, MAX_FINISHED_PER_USER_DAY)
                continue
            digest_job.enqueue(user_id=user_id, book_id=book_id, upto=n,
                               followups=(digest_job.FOLLOWUP_PREVIOUSLY,))
    except Exception:  # noqa: BLE001
        logger.exception("finished-book digest enqueue failed")


@event.listens_for(Session, "after_rollback")
def _drop_finished_books(session: Session) -> None:
    session.info.pop(_FINISHED_KEY, None)
