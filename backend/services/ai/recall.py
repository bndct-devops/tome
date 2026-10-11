"""Recall: one line on a name, place or term (feature ``recall``).

1. Look the term up in the reader's glossary (the fold up to their position
   chapter): exact name (case and whitespace folded), else the first word of a
   multi-word name ("Juliet" finds "Juliet Capulet"). A hit costs nothing.
2. Miss: if the term does not occur in the current chapter's text up to the
   reader's position, answer ``found: false`` without a call. Otherwise one
   small live call with that text (last 6000 words) and the glossary lines,
   asking for one line plus a verbatim ``evidence`` quote; the quote must
   occur in the text sent and name the entry (``digest.check_entries``) or
   the answer is dropped (``found: false``). Never a guess.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.user import User
from backend.services import ai
from backend.services.ai import companion, digest

FEATURE = "recall"
MAX_TERM_CHARS = 120
MAX_TOKENS = 8000
NOT_FOUND_MESSAGE = "Nothing about that before this page."

_POSSESSIVE_RE = re.compile(r"(?:'s|’s|'|’)$", re.IGNORECASE)

SYSTEM_PROMPT = """\
You answer one lookup for a spoiler-safe reading companion: who or what is a \
name, place or term, as of the point where the reader stopped.

You receive the term, the glossary so far (names with one-line descriptions \
from earlier chapters), and the text of the current chapter up to the \
reader's position.

Return:
- found: true only if the supplied text says something about the term. If it \
does not, return found false and leave the other fields empty.
- name: the term as the text writes it.
- kind: person, place or term.
- line: one sentence, at most 25 words, saying who or what it is as of the \
end of the supplied text.
- more: one or two sentences of fuller context from the supplied text and \
glossary, or empty.
- evidence: a short quote, at most 20 words, copied character for character \
from the supplied chapter text, that mentions the term. Never paraphrase it.

Use only the supplied text and glossary. Even if you recognise the book, \
never use what you know about it from elsewhere, and never mention anything \
that has not happened by the end of the supplied text. Plain hyphens, never \
em-dashes. Return only the JSON the schema describes."""


def build_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": list(digest.KINDS)},
            "line": {"type": "string"},
            "more": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["found", "name", "kind", "line", "more", "evidence"],
        "additionalProperties": False,
    }


class BadTerm(ValueError):
    pass


def clean_term(term: str) -> str:
    """Whitespace collapsed, edge punctuation and a trailing possessive
    stripped ("Tybalt's" -> "Tybalt"). Raises BadTerm when nothing is left."""
    t = " ".join((term or "").split())[:MAX_TERM_CHARS]
    t = t.strip(digest._EDGE_PUNCT + "‘’“”…")
    t = _POSSESSIVE_RE.sub("", t).strip(digest._EDGE_PUNCT)
    if not t or not re.search(r"\w", t):
        raise BadTerm("Select a name, place or term to look up.")
    return t


def find_entry(entries: list[digest.Entry], term: str) -> digest.Entry | None:
    """Exact (normalised) name first; else, for a one-word term, the first
    entry whose name starts with that word."""
    key = digest.normalize_name(term)
    for e in entries:
        if e.key == key:
            return e
    if len(key.split()) == 1:
        for e in entries:
            parts = e.key.split()
            if len(parts) > 1 and parts[0] == key:
                return e
    return None


def term_in_text(term: str, text: str) -> bool:
    needle = digest.normalize_text(term)
    if not needle:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", digest.normalize_text(text)) is not None


def recall(db: Session, user: User, book: Book, window: companion.Window, term: str) -> dict[str, Any]:
    """``{found, live, entry?, message?, term, chapter_idx, chapter_title}``.

    Raises BadTerm, ``AIDisabled`` when Recall is off, and the typed
    ``AIError``s of the live call (``AINotConfigured`` without a key)."""
    ai.check_feature_available(db, FEATURE)
    term = clean_term(term)
    base: dict[str, Any] = {"term": term, "chapter_idx": window.chapter_idx,
                            "chapter_title": window.chapter_title}

    scope = digest.user_scope(db, user, book)
    entries = digest.glossary_state(db, user, book, window.chapter_idx, scope=scope)
    hit = find_entry(entries, term)
    if hit is not None:
        [entry] = companion.entries_to_dicts(db, scope, [hit])
        return {**base, "found": True, "live": False, "entry": entry}

    text = companion.window_text(book, window, db)
    if not term_in_text(term, text):
        return {**base, "found": False, "live": False, "message": NOT_FOUND_MESSAGE}

    state = [{"name": e.name, "kind": e.kind, "line": e.line}
             for e in sorted(entries, key=lambda e: e.last_seen)[-digest.GLOSSARY_STATE_MAX:]]
    context = {
        "term": term,
        "book": book.title,
        "series": book.series,
        "volume": book.series_index,
        "current_chapter": {"number": window.chapter_idx + 1, "of": len(window.chapters),
                            "title": window.chapter_title},
        "glossary_so_far": state,
    }
    # The chapter text goes first and is marked for prompt caching: it is the
    # same for every term looked up at this position, so a reader looking up
    # several names in one sitting pays for it once. The term comes after it.
    content = [
        {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "The block above is the current chapter's text up to the "
                                 "reader's position. Look up this term. Context as JSON:\n\n"
                                 + json.dumps(context, ensure_ascii=False, indent=1)},
    ]
    result = ai.run_feature(db, user, FEATURE, system=SYSTEM_PROMPT, user_content=content,
                            schema=build_schema(), max_tokens=MAX_TOKENS)
    parsed = result.parsed or {}
    checked = digest.check_entries([{
        "name": parsed.get("name") or term, "kind": parsed.get("kind"),
        "line": parsed.get("line"), "more": parsed.get("more") or "",
        "evidence": parsed.get("evidence"),
    }], text) if parsed.get("found") is True else []
    if not checked:
        return {**base, "found": False, "live": True, "message": NOT_FOUND_MESSAGE}
    e = checked[0]
    where = {"book_id": book.id, "book_title": book.title,
             "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title}
    entry = {**e, "first_seen": where, "last_seen": where}
    return {**base, "found": True, "live": True, "entry": entry}
