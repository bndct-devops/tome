"""Per-chapter digests: the mechanism under recap, glossary and Recall.

A digest is the model's reading of ONE chapter: a short summary (for the
recap roll-up) and the glossary delta of that chapter (names, places, terms,
each with one line, a fuller ``more`` for new names and a verbatim
``evidence`` quote). Digests are user-independent and stored once per
(book, chapter) in ``ai_chapter_digests``; what a user sees is a fold over
the digests in their scope.

Calls. The missing chapters are grouped into units of consecutive chapters
of one volume (``plan_units``: at most ``CHUNK_CHAPTERS`` chapters and
``CHUNK_WORDS`` words); a unit of several chapters is one call
(``digest_chapters``), a unit of one uses ``digest_chapter``. What is sent:
the glossary state as names and kinds only (``model_state``, in its own
cached blocks first, ``state_blocks``), the context, then the chapter text.

Spoiler safety is structural:

- A call sends the text of its chapters and the glossary state folded from
  the chapters BEFORE the first of them in reading order, nothing else.
- A multi-chapter call sees chapters after the one it describes, so each of
  its rows records the call's last chapter (``context_end_idx``); for the
  current book a row is used only by readers past that chapter
  (``row_usable``), and re-read singly for the others.
- The triggers only ever ask for chapters the reader has finished: a reader
  in chapter k (``book_text.chapter_for_fraction`` of the position as a word
  fraction, ``book_text.reading_fraction``) gets digests of chapters
  ``0..k-1`` of the current book (``upto=k``, exclusive); an explicit
  series-scope start adds every chapter of the earlier volumes they marked
  read. A finished book gets all of its chapters.
- Every entry's ``evidence`` must be a quote of at least three words that
  occurs in the text of the chapter it is attributed to, on word boundaries
  (whitespace, case and quote style normalised), and that names the entry;
  otherwise the entry is dropped before storage (``check_entries``). In a
  multi-chapter call each entry is checked against its own chapter only.

Reading order across a series: earlier volumes (same ``series``, lower
``series_index``) in index order, each chapter in ``idx`` order, then the
current book. Which earlier volumes count is per user (``user_scope``): only
those with ``UserBookStatus.status == "read"``; the rest are listed as
excluded.

Triggers: ``on_position_advanced`` (called by the plugin position PUT and the
web status PUT; the current book's finished chapters only, once at least
``PENDING_MIN`` are missing) and a book becoming "read" through any write
path (an ORM flush hook on ``UserBookStatus.status``, enqueued after the
commit; capped per write and per user per day), which digests the whole book
(this book only, never its earlier volumes) and then runs the
``previously_recap`` follow-up the recap module registers with
``digest_job.register_followup`` (skipped when the book's own digests are
incomplete). Neither fires again for a book whose
latest job the user cancelled. Reading the glossary and asking for a recap
never queue work; ``POST /api/ai/books/{id}/digest`` does.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
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
# Earlier-volume entries in the state sent to the model (the most recently
# seen ones); every entry seen in the current volume is sent as well.
GLOSSARY_STATE_MAX = 150
STATE_CHUNK = 50               # state lines per content block (see ``state_blocks``)
MAX_ENTRIES_PER_CHAPTER = 25
MAX_TOKENS = 6000              # one chapter; several: ``multi_max_tokens``
NO_MODEL = "none"              # AIChapterDigest.model for a chapter too short to digest
FAILED_MODEL = "failed"        # a billed answer Tome could not use (refusal, cut off): not re-sent

# Units: the missing chapters of a volume go to the model in groups of
# consecutive chapters (``plan_units``), so a finished volume is a handful of
# calls instead of one per chapter.
CHUNK_CHAPTERS = 12
CHUNK_WORDS = 60_000
# The position trigger waits until this many finished chapters are missing.
PENDING_MIN = 3
# ... and leaves a larger backlog than this to the explicit start, which asks
# first. The frontend uses the same number (``CONFIRM_THRESHOLD`` in
# frontend/src/lib/ai.ts): above it the user confirms the estimate.
CONFIRM_THRESHOLD = 20

_NAME_MAX = 120
_LINE_MAX = 300
_MORE_MAX = 800
_SUMMARY_MAX = 1000

_PROMPT_INTRO_ONE = """\
You read one chapter of a book for a spoiler-safe reading companion. Your \
output feeds a recap and a glossary that a reader opens in the middle of the \
book, so it must contain nothing beyond the chapter you are given.

You receive the glossary so far (the name and kind of every person, place and \
term from the chapters before this one, in order of first appearance), the \
book's title, the chapter's position and title, and the full text of this \
chapter.

Return:"""

_PROMPT_INTRO_MANY = """\
You read several consecutive chapters of a book for a spoiler-safe reading \
companion. Your output feeds a recap and a glossary that a reader opens in the \
middle of the book, so what you say about a chapter must contain nothing \
beyond the end of that chapter.

You receive the glossary so far (the name and kind of every person, place and \
term from the chapters before these, in order of first appearance), the \
book's title, and the chapters, each in its own block headed \
"=== [cN] Title ===", where N in brackets identifies the block.

Return chapters: one item per block, in order. Set chapter to the number in \
brackets of the block this answer is for (for "=== [c7] ... ===" that is 7), \
never a count of the blocks and never a number from the chapter's own title. \
For each:"""

_PROMPT_FIELDS = """
- summary: at most 3 sentences on what happens in this chapter, in the past \
tense. Plain and factual: no judgement, no foreshadowing, no hints about what \
comes later, nothing about the book's reputation.
- entries: at most 25. The people, places and terms (kind: person, place or \
term) this chapter introduces, plus names already in the glossary whose \
description changes because of this chapter. List a name already in the \
glossary only when its description changes, and repeat it exactly as written \
there. For each entry:
  - line: one sentence, at most 25 words, saying who or what it is as of the \
end of this chapter.
  - more: for a name new in this chapter, one or two sentences of fuller \
context from this chapter. For a name already in the glossary, an empty string.
  - evidence: a quote of 3 to 20 words, copied character for character \
from this chapter's text, that contains the entry's name (or a distinctive \
part of it) as the text writes it. A line a character speaks does not count \
unless the name is in it; in a play, quote a stage direction or another \
character's line that names them. Never paraphrase it."""

_PROMPT_MANY_RULES = """
- Work through the chapters in order. A name introduced in an earlier chapter \
of this request counts as already in the glossary for the later ones.
- What you write for a chapter uses only that chapter and what came before \
it, never a later chapter in this request. Its evidence is copied from that \
chapter's own text."""

_PROMPT_RULES = """

Rules:
- The chapter's viewpoint character and main characters are entries whenever \
the text names them, even when they seem obvious.
- Use only the supplied text and glossary. Even if you recognise the book, \
never use what you know about it from elsewhere, and never mention anything \
that has not happened by the end of the chapter.
- Names as the text writes them. A narrator speaking as "I" is an entry only \
when the text gives a name.
- No entry for the author, the book itself, or ordinary words.
- Front or back matter (title page, contents, a note about the author, \
afterword, publisher's notes): an empty summary and no entries."""

SYSTEM_PROMPT = (_PROMPT_INTRO_ONE + _PROMPT_FIELDS + _PROMPT_RULES
                 + "\n- Return only the JSON the schema describes.")
SYSTEM_PROMPT_MANY = (_PROMPT_INTRO_MANY + _PROMPT_FIELDS + _PROMPT_RULES + _PROMPT_MANY_RULES
                      + "\n- Return only the JSON the schema describes.")


def _entries_schema() -> dict[str, Any]:
    return {
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
    }


def build_schema() -> dict[str, Any]:
    """One chapter: ``{summary, entries}``."""
    return {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "entries": _entries_schema()},
        "required": ["summary", "entries"],
        "additionalProperties": False,
    }


def build_multi_schema() -> dict[str, Any]:
    """Several chapters: ``{chapters: [{chapter, summary, entries}]}``."""
    return {
        "type": "object",
        "properties": {
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chapter": {"type": "integer",
                                    "description": "The number in brackets of the block "
                                                   "this answer is for: 7 for \"=== [c7] ... ===\"."},
                        "summary": {"type": "string"},
                        "entries": _entries_schema(),
                    },
                    "required": ["chapter", "summary", "entries"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["chapters"],
        "additionalProperties": False,
    }


# Output room per chapter in a multi-chapter call. 700 was too tight: on the
# first real run Haiku spent 1100-1300 tokens a chapter on three acts of Romeo
# and Juliet, hit the 3600 cap, and the whole unit was billed and stored as
# failed. 25 entries with a line, a ``more`` and a quote need about 2000.
MULTI_TOKENS_PER_CHAPTER = 2500


def multi_max_tokens(n: int) -> int:
    return min(32_000, 1500 + MULTI_TOKENS_PER_CHAPTER * n)


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


def evidence_ok(name: str, evidence: str, normalized_chapter: str) -> bool:
    """The citation check for one entry: the quote occurs in the chapter on
    word boundaries and names the entry. A quote shorter than
    ``MIN_EVIDENCE_WORDS`` passes only when it holds the entry's whole name
    (of three or more letters; two for a CJK name) as whole words, so
    "Enter ROMEO" keeps Romeo in a play, while a stray fragment still never
    carries an entry. Such a short quote is not stored as it is:
    ``check_entries`` widens it (``widen_evidence``)."""
    ev = normalize_text(evidence)
    if not ev:
        return False
    if book_text.count_words(ev) < MIN_EVIDENCE_WORDS:
        n = normalize_text(name)
        min_len = 2 if _CJK_RE.search(n) else 3
        return len(normalize_name(name)) >= min_len and _find_word(n, ev) and _find_word(ev, normalized_chapter)
    return _find_word(ev, normalized_chapter) and names_entry(name, evidence)


WIDE_EVIDENCE_WORDS = 12       # a widened short quote: 3 to 12 words of the chapter
_LOOSE_CHARS = {"'": "['‘’‚‛′]", '"': '["“”„‟″«»]', "-": "[-–—―−]"}
_SENTENCE_END_RE = re.compile(r"[.!?\n。！？]")


def _loose_pattern(quote: str) -> re.Pattern[str] | None:
    """``quote`` as a case-insensitive pattern over the ORIGINAL text: any
    run of whitespace between its words, typographic quotes and dashes for
    their ASCII forms, whole words at a non-CJK edge."""
    q = " ".join(quote.translate(_QUOTE_MAP).split())
    if not q:
        return None
    body = r"\s+".join("".join(_LOOSE_CHARS.get(ch, re.escape(ch)) for ch in tok) for tok in q.split(" "))
    left = "" if _CJK_RE.match(q[0]) else r"(?<!\w)"
    right = "" if _CJK_RE.match(q[-1]) else r"(?!\w)"
    return re.compile(left + body + right, re.IGNORECASE)


def widen_evidence(name: str, evidence: str, chapter_text: str) -> str | None:
    """A quote under ``MIN_EVIDENCE_WORDS`` (a bare "ROMEO") widened to a
    verbatim slice of ``chapter_text``: its first occurrence (case and
    whitespace tolerant) plus the following words (the preceding ones at the
    chapter's end), cut on word starts, 3 to ``WIDE_EVIDENCE_WORDS`` words,
    stopping at a sentence end once there are three. None when the quote is
    not found or the slice does not pass the normal check."""
    pat = _loose_pattern(evidence)
    m = pat.search(chapter_text) if pat else None
    if m is None:
        return None
    spans = [(w.start(), w.end()) for w in book_text._WORD_RE.finditer(chapter_text)]
    inside = [i for i, (a, _) in enumerate(spans) if m.start() <= a < m.end()]
    if not inside:
        return None
    first, last = inside[0], inside[-1]
    while last + 1 < len(spans) and last - first + 1 < WIDE_EVIDENCE_WORDS:
        if (last - first + 1 >= MIN_EVIDENCE_WORDS
                and _SENTENCE_END_RE.search(chapter_text, spans[last][1], spans[last + 1][0])):
            break
        last += 1
    while first > 0 and last - first + 1 < MIN_EVIDENCE_WORDS:
        first -= 1
    quote = " ".join(chapter_text[spans[first][0]:spans[last][1]].split())
    if not evidence_in_text(quote, normalize_text(chapter_text)) or not names_entry(name, quote):
        return None
    return quote


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


_NAME_TRAIL = ".,;:!?"
_BRACKETS = (("(", ")"), ("[", "]"))


def clean_name(name: Any) -> str:
    """The model's name as the glossary keeps it: whitespace collapsed,
    trailing sentence punctuation dropped, and a closing ``)`` / ``]`` at the
    end (or an opening one at the start) dropped only when the name has no
    matching partner. Brackets and quotes that are part of the name stay:
    "Vane Estate (Hedge Maze)" is kept whole."""
    n = _str(name, _NAME_MAX)
    while True:
        before = n
        n = n.strip().rstrip(_NAME_TRAIL).strip()
        for opener, closer in _BRACKETS:
            if n.endswith(closer) and n.count(opener) < n.count(closer):
                n = n[:-1]
            if n.startswith(opener) and n.count(opener) > n.count(closer):
                n = n[1:]
        if n == before:
            return n


def check_entries(raw_entries: Any, chapter_text: str,
                  known: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Validate the model's entries against the chapter text it was given.

    Kept: a non-empty name (``clean_name``), a known kind, a line, and an
    ``evidence`` quote of at least three words that occurs in
    ``chapter_text`` on word boundaries (normalised) and names the entry
    (``names_entry``), so a real sentence about someone else cannot carry an
    invented entry. Evidence longer than 20 words is cut to its first 20
    words (still a verbatim slice); a shorter quote that passes on the
    entry's whole name (``evidence_ok``) is replaced by a slice of 3 to 12
    words of ``chapter_text`` around it (``widen_evidence``). One entry per
    name, at most ``MAX_ENTRIES_PER_CHAPTER``.

    ``known`` maps the normalised names already seen in the CURRENT volume
    to their current line: such an entry is dropped when its line did not
    change. ``more`` is kept as the model gave it (capped): a reader whose
    scope lacks the volume that introduced a name still needs it, and the
    fold keeps the first non-empty one. Everything else is dropped."""
    if not isinstance(raw_entries, list):
        return []
    known = known or {}
    haystack = normalize_text(chapter_text)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        name = clean_name(raw.get("name"))
        key = normalize_name(name)
        if not key or key in seen:
            continue
        kind = raw.get("kind") if raw.get("kind") in KINDS else None
        line = _str(raw.get("line"), _LINE_MAX)
        if kind is None or not line:
            continue
        evidence = _clean_evidence(raw.get("evidence") if isinstance(raw.get("evidence"), str) else "")
        if not evidence_ok(name, evidence, haystack):
            continue
        if book_text.count_words(normalize_text(evidence)) < MIN_EVIDENCE_WORDS:
            evidence = widen_evidence(name, evidence, chapter_text) or ""
            if not evidence:
                continue
        more = _str(raw.get("more"), _MORE_MAX)
        if key in known and normalize_text(known[key]) == normalize_text(line):
            continue
        seen.add(key)
        out.append({"name": name, "kind": kind, "line": line, "more": more, "evidence": evidence})
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
    chapters: int = 1             # distinct chapters that listed it (first + updates)

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "line": self.line, "more": self.more,
            "evidence": self.evidence,
            "first_seen": {"book_id": self.first_seen[0], "chapter_idx": self.first_seen[1]},
            "last_seen": {"book_id": self.last_seen[0], "chapter_idx": self.last_seen[1]},
            "chapters": self.chapters,
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
    ``line``/``evidence`` replaces the earlier one (and its ``kind``); the
    first non-empty ``more`` in the folded set is kept;
    ``first_seen`` and the first spelling of the name are kept; ``chapters``
    counts the distinct chapters that listed the name (the book page sorts
    people by it). Result is in order of first appearance. Pure and
    deterministic."""
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
                if where != cur.last_seen:
                    cur.chapters += 1
                cur.line = e["line"]
                cur.more = cur.more or e.get("more") or ""   # the first fuller context wins
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


def saw_later_chapters(d: Any) -> bool:
    """The call that wrote ``d`` also saw chapters after it (a multi-chapter
    call: ``context_end_idx`` past ``chapter_idx``)."""
    end = getattr(d, "context_end_idx", None)
    return end is not None and end > d.chapter_idx


def row_usable(d: Any, upto: int) -> bool:
    """A digest of the CURRENT book is usable for a reader whose position
    chapter is ``upto`` only when its call saw nothing at or past ``upto``:
    a multi-chapter call for chapters 12..23 wrote chapter 19 having read
    20..23, so a reader in chapter 20 must not get it (its summary or lines
    may carry what comes later). Earlier volumes are read in full by
    definition, so this applies to the current book only."""
    end = getattr(d, "context_end_idx", None)
    return end is None or end <= d.chapter_idx or end < upto


def _usable_by_book(db: Session, scope: Scope, upto: int) -> dict[int, dict[int, AIChapterDigest]]:
    """``_digests_by_book`` for the scope's volumes, without the current
    book's rows a reader at ``upto`` must not use (``row_usable``)."""
    have = _digests_by_book(db, [b.id for b in scope.volumes()])
    cur = have.get(scope.book.id, {})
    for idx in [i for i, d in cur.items() if not row_usable(d, upto)]:
        del cur[idx]
    return have


def _stale_for(db: Session, scope: Scope, upto: int) -> set[int]:
    """Chapters of the current book before ``upto`` that have a stored row
    the reader cannot use (``row_usable``): re-read singly for them."""
    rows = _digests_by_book(db, [scope.book.id])[scope.book.id]
    return {i for i, d in rows.items() if i < upto and not row_usable(d, upto)}


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
    """The stored digests the user's fold runs over, in reading order (only
    rows usable at ``upto``, ``row_usable``)."""
    pairs = scope_chapters(db, scope, upto)
    have = _usable_by_book(db, scope, upto)
    return [have[b.id][c.idx] for b, c in pairs if c.idx in have.get(b.id, {})]


def missing_chapters(db: Session, scope: Scope, upto: int) -> list[tuple[Book, BookChapter]]:
    """Pairs in the scope that have no digest yet, in reading order. A row the
    reader cannot use (``row_usable``) counts as missing."""
    pairs = scope_chapters(db, scope, upto)
    have = _usable_by_book(db, scope, upto)
    return [(b, c) for b, c in pairs if c.idx not in have.get(b.id, {})]


def coverage(db: Session, scope: Scope, upto: int) -> dict[str, int]:
    """``digested`` counts every stored row (a ``FAILED_MODEL`` placeholder
    too: it is done and never re-sent); ``failed`` is how many of those are
    placeholders, so ``digested - failed`` chapters actually feed the fold.
    Rows the reader cannot use (``row_usable``) are not counted."""
    pairs = scope_chapters(db, scope, upto)
    have = _usable_by_book(db, scope, upto)
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
    have = _usable_by_book(db, scope, upto)
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


# ── units: how the missing chapters are grouped into calls ──────────────────

@dataclass(frozen=True)
class Unit:
    """Consecutive missing chapters of one volume, digested in one call
    (``digest_chapter`` for one, ``digest_chapters`` for several)."""
    book_id: int
    idxs: tuple[int, ...]
    words: int          # words sent (each chapter capped at MAX_CHAPTER_WORDS)
    # One chapter whose stored row this reader cannot use (``row_usable``):
    # read again on its own and the row overwritten.
    replace: bool = False


class UnitSplit(Exception):
    """A multi-chapter call whose answer Tome could not use (a refusal, an
    answer cut off, or chapter numbers it cannot place). Nothing was stored;
    the runner reads ``units`` (one chapter each) next, in the same job."""

    def __init__(self, units: list[Unit], reason: str) -> None:
        super().__init__(reason)
        self.units = units
        self.reason = reason


def plan_units(db: Session, scope: Scope, upto: int) -> list[Unit]:
    """The missing chapters of ``scope`` up to ``upto`` (``missing_chapters``,
    reading order) grouped into units: consecutive chapters of one volume, at
    most ``CHUNK_CHAPTERS`` chapters and ``CHUNK_WORDS`` words each. A chapter
    longer than ``CHUNK_WORDS`` is a unit of its own (and truncated to
    ``MAX_CHAPTER_WORDS`` when sent, as always). A gap (a chapter already
    stored) ends a unit. A chapter of the current book whose stored row the
    reader cannot use (``row_usable``: its multi-chapter call saw chapters at
    or past ``upto``) is a unit of its own with ``replace`` set, so its new
    row is valid for every reader."""
    stale = _stale_for(db, scope, upto)
    words_by_book: dict[int, dict[int, int]] = {}
    units: list[Unit] = []
    cur: list[int] = []
    cur_book: int | None = None
    cur_words = 0

    def flush() -> None:
        nonlocal cur, cur_words
        if cur and cur_book is not None:
            units.append(Unit(book_id=cur_book, idxs=tuple(cur), words=cur_words))
        cur, cur_words = [], 0

    for vol, ch in missing_chapters(db, scope, upto):
        if vol.id not in words_by_book:
            words_by_book[vol.id] = book_text.chapter_word_counts(vol, db)
        words = words_by_book[vol.id].get(ch.idx, 0)
        sent = min(words, MAX_CHAPTER_WORDS)
        if vol.id == scope.book.id and ch.idx in stale:
            flush()
            units.append(Unit(book_id=vol.id, idxs=(ch.idx,), words=sent, replace=True))
            continue
        if words > CHUNK_WORDS:
            flush()
            units.append(Unit(book_id=vol.id, idxs=(ch.idx,), words=sent))
            continue
        if cur and (vol.id != cur_book or ch.idx != cur[-1] + 1 or len(cur) >= CHUNK_CHAPTERS
                    or cur_words + sent > CHUNK_WORDS):
            flush()
        cur_book = vol.id
        cur.append(ch.idx)
        cur_words += sent
    flush()
    return units


# ── what the model is sent ───────────────────────────────────────────────────

def get_digest(db: Session, book_id: int, idx: int) -> AIChapterDigest | None:
    return (
        db.query(AIChapterDigest)
        .filter(AIChapterDigest.book_id == book_id, AIChapterDigest.chapter_idx == idx)
        .first()
    )


def model_state(prior: list[AIChapterDigest], book_id: int) -> list[dict[str, str]]:
    """The glossary state sent with a digest call: ``[{name, kind}]``, no
    descriptions.

    ``prior`` is ``_prior_digests`` (reading order). Sent: the
    ``GLOSSARY_STATE_MAX`` entries of the earlier volumes seen most recently
    in them (in their first-seen order), then every entry seen in this
    volume's chapters so far, in order of its first appearance in this
    volume (a name from an earlier volume that comes back here is listed
    there when it was not among the earlier ones). Each name keeps its
    spelling and kind from where it is listed, so the list only grows at the
    end from one chapter of a volume to the next, and its stable prefix is
    cached (``state_blocks``)."""
    rank = {(int(d.book_id), int(d.chapter_idx)): i for i, d in enumerate(prior)}
    earlier = fold_entries(d for d in prior if d.book_id != book_id)
    top = sorted(earlier, key=lambda e: rank.get(e.last_seen, -1))[-GLOSSARY_STATE_MAX:]
    listed = {e.key for e in top}
    state = [{"name": e.name, "kind": e.kind} for e in earlier if e.key in listed]
    spelling = {e.key: e.name for e in earlier}
    for d in prior:
        if d.book_id != book_id:
            continue
        for e in digest_entries(d):
            if not isinstance(e, dict):
                continue
            name = " ".join(str(e.get("name", "")).split())
            key = normalize_name(name)
            if not key or key in listed:
                continue
            listed.add(key)
            state.append({"name": spelling.get(key, name),
                          "kind": e.get("kind") if e.get("kind") in KINDS else "term"})
    return state


STATE_HEADER = ("Glossary so far: every name, place and term from the chapters before, "
                "in order of first appearance, one JSON object per line.")


def state_blocks(state: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The state as the FIRST content blocks of a digest call, ``STATE_CHUNK``
    lines per block. The list only grows at its end, so every full block is
    byte-identical from one call to the next; the last full block carries
    ``cache_control`` (a cache hit can only land on a block boundary a
    previous call marked, so one growing block would never be read back).
    The partial tail, the context and the chapter text follow uncached."""
    if not state:
        return [{"type": "text", "text": STATE_HEADER + "\n(none yet)"}]
    lines = [json.dumps(s, ensure_ascii=False) for s in state]
    blocks: list[dict[str, Any]] = []
    for i in range(0, len(lines), STATE_CHUNK):
        text = "\n".join(lines[i:i + STATE_CHUNK])
        blocks.append({"type": "text", "text": (STATE_HEADER + "\n" + text) if i == 0 else text})
    full = len(lines) // STATE_CHUNK
    if full:
        blocks[full - 1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def chapter_number(chapter: BookChapter) -> int:
    """The number a multi-chapter call identifies a chapter by (``[cN]``)."""
    return chapter.idx + 1


def chapter_heading(chapter: BookChapter) -> str:
    """``=== [c7] Title ===``: a bracketed token the model echoes back as
    ``chapter``, unmistakable for a position in the request or a number in
    the chapter's own title."""
    title = " ".join((chapter.title or "").split())
    n = chapter_number(chapter)
    return f"=== [c{n}] {title} ===" if title else f"=== [c{n}] ==="


def build_user_content(book: Book, chapter: BookChapter, chapter_count: int,
                       state: list[dict[str, str]], text: str) -> list[dict[str, Any]]:
    """One chapter: the state blocks, the context (book, chapter), then the
    chapter text on its own, verbatim."""
    context = {
        "book": book.title,
        "series": book.series,
        "volume": book.series_index,
        "chapter": {"number": chapter.idx + 1, "of": chapter_count, "title": chapter.title},
    }
    return state_blocks(state) + [
        {"type": "text", "text": "Digest this chapter. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)
                                 + "\n\nThe chapter text follows in the next block."},
        {"type": "text", "text": text},
    ]


def build_multi_user_content(book: Book, chapter_count: int, state: list[dict[str, str]],
                             items: list[tuple[BookChapter, str]]) -> list[dict[str, Any]]:
    """Several chapters: the state blocks, the context, then one block per
    chapter, ``=== [cN] Title ===`` and its text."""
    context = {
        "book": book.title,
        "series": book.series,
        "volume": book.series_index,
        "chapters_in_book": chapter_count,
    }
    return state_blocks(state) + [
        {"type": "text", "text": f"Digest these {len(items)} chapters. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)
                                 + "\n\nEach chapter follows in its own block."},
    ] + [{"type": "text", "text": chapter_heading(ch) + "\n\n" + text} for ch, text in items]


# ── digesting: one call per unit ─────────────────────────────────────────────

_ROW_FIELDS = ("model", "summary", "entries_json", "input_tokens", "output_tokens",
               "context_end_idx")


def _store(db: Session, row: AIChapterDigest) -> AIChapterDigest:
    """Insert ``row``; when the chapter is stored already, keep that row,
    except that a row written by a call that saw later chapters
    (``saw_later_chapters``) is overwritten by one that did not: a
    single-chapter digest is valid for every reader."""
    existing = get_digest(db, row.book_id, row.chapter_idx)
    if existing is not None:
        if saw_later_chapters(existing) and not saw_later_chapters(row):
            for name in _ROW_FIELDS:
                setattr(existing, name, getattr(row, name))
            existing.created_at = datetime.utcnow()
            db.commit()
        return existing
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


def apportion(total: int, weights: list[int]) -> list[int]:
    """``total`` split by ``weights`` into integers that sum to ``total``
    (largest remainder)."""
    if not weights:
        return []
    w = [max(1, x) for x in weights]
    s = sum(w)
    raw = [total * x / s for x in w]
    out = [int(r) for r in raw]
    for i in sorted(range(len(w)), key=lambda i: raw[i] - out[i], reverse=True)[:total - sum(out)]:
        out[i] += 1
    return out


def _billed(result: Any) -> tuple[int, int]:
    return (result.input_tokens + result.cache_read_tokens + result.cache_write_tokens,
            result.output_tokens)


@dataclass
class _Sent:
    chapter: BookChapter
    text: str      # what the model saw (capped at MAX_CHAPTER_WORDS)
    words: int


def _single_units(book: Book, sent: list[_Sent]) -> list[Unit]:
    return [Unit(book_id=book.id, idxs=(s.chapter.idx,), words=s.words) for s in sent]


def match_answers(items: Any, sent: list[_Sent]) -> list[tuple[_Sent, dict[str, Any]]] | None:
    """The items of a multi-chapter answer paired with the chapters sent, by
    the ``[cN]`` number each item gives. None when the answer cannot be
    placed: an item that is not an object, a number that was not sent, or a
    number given twice (a model numbering by position in the request or by
    the chapter's own title would otherwise store a summary under the wrong
    chapter). A chapter the answer leaves out is simply missing."""
    by_number = {chapter_number(s.chapter): s for s in sent}
    seen: dict[int, dict[str, Any]] = {}
    for item in items if isinstance(items, list) else []:
        num = item.get("chapter") if isinstance(item, dict) else None
        if not isinstance(num, int) or isinstance(num, bool) or num not in by_number or num in seen:
            return None
        seen[num] = item
    return [(s, seen[chapter_number(s.chapter)]) for s in sent if chapter_number(s.chapter) in seen]


def _digest_unit(db: Session, user: User, book: Book, chapter_count: int,
                 sent: list[_Sent], job_id: str | None = None) -> list[AIChapterDigest]:
    """One model call for ``sent`` (consecutive chapters of ``book``, each
    long enough to digest). Stores one row per chapter the answer covers and
    returns them; a chapter the answer leaves out is not stored (retryable).

    The model sees the state folded from the digests before the first of
    them (``_prior_digests``: the series' reading order, not a user's scope,
    since a digest is shared by every reader). Each chapter's entries are
    checked against that chapter's own text (``check_entries``); a name
    already seen in THIS volume (an earlier chapter, or an earlier chapter of
    this call) is dropped when its line did not change. The call's tokens are
    split over the stored rows by each chapter's share of the words sent.
    One usage row per call (``ai.run_feature``), carrying ``job_id`` and
    this book's id. Every row of a multi-chapter call records the call's
    last chapter in ``context_end_idx`` (``row_usable``).

    A multi-chapter call whose answer Tome cannot use (a refusal, an answer
    cut off or unreadable, or chapter numbers it cannot place,
    ``match_answers``) stores nothing and raises ``UnitSplit`` with one unit
    per chapter, which the runner reads next. A single chapter's billed
    answer Tome cannot use is the same on every retry, so it is stored as a
    ``FAILED_MODEL`` placeholder (no summary, no entries) before the error
    is re-raised: never re-sent by a poll or a trigger, reported as
    ``failed`` by coverage; "Ask again" on the recap clears it. Errors
    without a billed answer (outage, rate limit, no key) store nothing."""
    multi = len(sent) > 1
    prior = _prior_digests(db, book, sent[0].chapter.idx)
    state = model_state(prior, book.id)
    known = {e.key: e.line for e in fold_entries(d for d in prior if d.book_id == book.id)}
    context_end = sent[-1].chapter.idx if multi else None
    if multi:
        call = dict(system=SYSTEM_PROMPT_MANY, schema=build_multi_schema(),
                    max_tokens=multi_max_tokens(len(sent)),
                    user_content=build_multi_user_content(
                        book, chapter_count, state, [(s.chapter, s.text) for s in sent]))
    else:
        call = dict(system=SYSTEM_PROMPT, schema=build_schema(), max_tokens=MAX_TOKENS,
                    user_content=build_user_content(book, sent[0].chapter, chapter_count,
                                                    state, sent[0].text))
    try:
        result = ai.run_feature(db, user, FEATURE, job_id=job_id, book_id=book.id, words=sum(s.words for s in sent),
                                chapters=len(sent), **call)
    except AIError as exc:
        billed = getattr(exc, "result", None)
        if billed is not None and not isinstance(exc, (AIDisabled, AINotConfigured, AIKeyInvalid)):
            # run_feature has already committed the usage row of this answer.
            if multi:
                raise UnitSplit(_single_units(book, sent), exc.message) from exc
            ins, outs = _billed(billed)
            _store(db, AIChapterDigest(
                book_id=book.id, chapter_idx=sent[0].chapter.idx, model=FAILED_MODEL, summary="",
                entries_json="[]", input_tokens=ins, output_tokens=outs))
        raise

    parsed = result.parsed or {}
    if multi:
        matched = match_answers(parsed.get("chapters"), sent)
        if matched is None:
            logger.warning("digest of book %s chapters %s: answer numbers do not match the "
                           "chapters sent; reading them one by one", book.id,
                           [s.chapter.idx for s in sent])
            raise UnitSplit(_single_units(book, sent), "The answer could not be matched to its chapters.")
        answered = matched
    else:
        answered = [(sent[0], parsed)]

    ins, outs = (apportion(t, [s.words for s, _ in answered]) for t in _billed(result))
    rows: list[AIChapterDigest] = []
    for (s, answer), i, o in zip(answered, ins, outs):
        entries = check_entries(answer.get("entries"), s.text, known)
        for e in entries:
            known[normalize_name(e["name"])] = e["line"]
        rows.append(_store(db, AIChapterDigest(
            book_id=book.id, chapter_idx=s.chapter.idx, model=result.model,
            summary=_str(answer.get("summary"), _SUMMARY_MAX),
            entries_json=json.dumps(entries, ensure_ascii=False),
            input_tokens=i, output_tokens=o, context_end_idx=context_end,
        )))
    return rows


def _prepare(db: Session, book: Book, idx: int,
             chapters: list[BookChapter]) -> AIChapterDigest | _Sent:
    """A stored row for a chapter too short to digest, else what to send."""
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
    return _Sent(chapter=chapter, text=sent, words=book_text.count_words(sent))


def digest_chapter(db: Session, user: User, book: Book, idx: int, *,
                   job_id: str | None = None, replace_partial: bool = False) -> AIChapterDigest:
    """Digest chapter ``idx`` of ``book`` on its own (or return the stored
    digest): a unit of one chapter, the single-chapter prompt and schema.
    With ``replace_partial`` a stored row whose call saw later chapters
    (``saw_later_chapters``) is read again and overwritten (a ``replace``
    unit of ``plan_units``). Goes through ``ai.run_feature`` with feature
    ``reading_digest``. Raises ``DigestUnavailable`` and the typed
    ``AIError``s (see ``_digest_unit``)."""
    existing = get_digest(db, book.id, idx)
    if existing is not None and not (replace_partial and saw_later_chapters(existing)):
        return existing
    chapters = book_text.book_chapters(book, db)
    prepared = _prepare(db, book, idx, chapters)
    if isinstance(prepared, AIChapterDigest):
        return prepared
    [row] = _digest_unit(db, user, book, len(chapters), [prepared], job_id)
    return row


def digest_chapters(db: Session, user: User, book: Book, idxs: list[int], *,
                    job_id: str | None = None) -> list[AIChapterDigest]:
    """Digest several consecutive chapters of ONE book in one model call.

    Chapters already stored are skipped, chapters too short to digest are
    stored without a call; the rest go to the model together (a single one
    falls back to ``digest_chapter``'s prompt). Returns the stored rows of
    ``idxs`` in ``idx`` order: a chapter missing from the result was left
    out of the model's answer and is still undigested (retryable). Raises
    ``DigestUnavailable``, ``UnitSplit`` and the typed ``AIError``s (see
    ``_digest_unit``)."""
    idxs = sorted(set(idxs))
    have = _digests_by_book(db, [book.id])[book.id]
    chapters = book_text.book_chapters(book, db)
    rows: dict[int, AIChapterDigest] = {i: have[i] for i in idxs if i in have}
    sent: list[_Sent] = []
    for idx in idxs:
        if idx in rows:
            continue
        prepared = _prepare(db, book, idx, chapters)
        if isinstance(prepared, AIChapterDigest):
            rows[idx] = prepared
        else:
            sent.append(prepared)
    if sent:
        for row in _digest_unit(db, user, book, len(chapters), sent, job_id):
            rows[row.chapter_idx] = row
    return [rows[i] for i in idxs if i in rows]


# ── triggers ─────────────────────────────────────────────────────────────────

# (user_id, book_id) -> highest position chapter already acted on. In memory,
# single process (like the job): a restart costs at most one extra enqueue.
_advance_lock = threading.Lock()
_last_target: dict[tuple[int, int], int] = {}

# Finished-book jobs (whole-book digest + an Opus "Previously on") are capped:
# a reading-CSV import, a plugin sweep or a sync-code batch can mark hundreds
# of books read in one go, and none of that should turn into hundreds of
# unasked-for calls. The rest are skipped (logged); an explicit start
# (POST .../digest) still works for them.
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
    """Enqueue digests of the chapters the reader has finished, a few at a time.

    Called after the position is committed (plugin ``PUT
    /tome-sync/position/{id}`` with its xpointer, web ``PUT /books/{id}/status``
    with its CFI). ``pct`` is a page or section fraction, so the chapter comes
    from ``book_text.reading_fraction`` (the locator, else a margin), never
    from ``pct`` itself. Acts only when that chapter k (rounded down) is past
    chapter 0 and past the highest chapter already acted on for this user and
    book, the book is an EPUB with a chapter map, AI and ``reading_digest``
    are on, and the user's key resolves. Enqueues chapters ``< k`` of THIS
    book only, and only once at least ``PENDING_MIN`` of them are missing and
    not already covered by an active job (so the job reads them in one
    call), and at most ``CONFIRM_THRESHOLD`` (turning the feature on deep
    into a long book is a backlog the user confirms first); earlier volumes
    and larger backlogs are left to an explicit start (``POST .../digest``),
    which asks before spending. Never enqueues after the
    user's latest job for this book was cancelled (its ``ai_jobs`` row),
    until they start one again, nor when the work would pass a monthly
    spend limit. Never raises; returns the job snapshot or None."""
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
        from backend.services.ai import budget, digest_job, estimate
        if digest_job.last_cancelled(db, user.id, book.id):
            return None
        active = digest_job.active_for(user.id, book.id)
        covered = active["upto_chapter"] if active and active["status"] != "stopping" else 0
        pending = [c for _, c in missing_chapters(db, Scope(book=book), k) if c.idx >= covered]
        if not PENDING_MIN <= len(pending) <= CONFIRM_THRESHOLD:
            return None   # too few to batch, or a backlog the user confirms first
        words = book_text.chapter_word_counts(book, db)
        guess = estimate.call_cost_guess(
            FEATURE, ai_settings.effective_model(db, FEATURE),
            sum(min(words.get(c.idx, 0), MAX_CHAPTER_WORDS) for c in pending), len(pending))
        if not budget.allows(db, user, guess):
            logger.info("digest trigger skipped for user %s book %s: monthly AI budget", user.id, book.id)
            return None
        return digest_job.enqueue(user_id=user.id, book_id=book.id, upto=k, current_book_only=True)
    except Exception:  # noqa: BLE001 - never break a position sync
        logger.exception("digest trigger failed for user %s book %s", user.id, book.id)
        return None


def finished_book_job(db: Session, user: User, book: Book) -> tuple[int, int, int] | None:
    """(user_id, book_id, chapter_count) when a finished ``book`` should get its
    whole-book digest (this book only: earlier volumes always go through the
    explicit start and its dialog) and the "Previously on" recap, else None: the book is
    in a series, has chapter text, AI plus ``reading_digest`` and ``recap``
    are on, the user's key resolves, the user's latest job for the book was
    not cancelled, and a whole-book digest would not pass a monthly spend
    limit."""
    if not ai_settings.env_enabled() or not book.series:
        return None
    if book_text.unavailable_reason(book, db):
        return None
    if not _user_can_digest(db, user, FEATURE, RECAP_FEATURE):
        return None
    from backend.services.ai import budget, digest_job, estimate
    if digest_job.last_cancelled(db, user.id, book.id):   # stopped by the user: only they restart it
        return None
    n = len(book_text.book_chapters(book, db))
    if not n:
        return None
    # A cheap guess (no file read: this runs inside the write that finished
    # the book): the stored word count, every chapter in one call's terms.
    guess = estimate.call_cost_guess(FEATURE, ai_settings.effective_model(db, FEATURE),
                                     int(book.word_count or 0), n)
    if not budget.allows(db, user, guess):
        logger.info("finished-book digest skipped for user %s book %s: monthly AI budget",
                    user.id, book.id)
        return None
    return (user.id, book.id, n)


def on_book_finished(db: Session, user: User, book: Book) -> dict | None:
    """Enqueue the whole-book digest + ``previously_recap`` follow-up now.
    The ORM hook below calls the same gate and enqueues after commit; this
    direct form is for callers outside a write transaction."""
    job = finished_book_job(db, user, book)
    if job is None or not _finished_allowed(user.id, book.id):
        return None
    from backend.services.ai import digest_job
    return digest_job.enqueue(user_id=job[0], book_id=job[1], upto=job[2],
                              followups=(digest_job.FOLLOWUP_PREVIOUSLY,), current_book_only=True)


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
                               followups=(digest_job.FOLLOWUP_PREVIOUSLY,), current_book_only=True)
    except Exception:  # noqa: BLE001
        logger.exception("finished-book digest enqueue failed")


@event.listens_for(Session, "after_rollback")
def _drop_finished_books(session: Session) -> None:
    session.info.pop(_FINISHED_KEY, None)
