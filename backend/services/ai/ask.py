"""Ask about what you have read (feature ``ask``).

A free question answered from the text the reader has already read, never
past it. The window is the one Recall and the recap use
(``backend/services/ai/companion.py``): every chapter of the read earlier
volumes in the user's scope, this book's chapters before the position
chapter, and the position chapter's text cut at the position
(``companion.window_text``). Nothing at or past the position is ever read.

1. Shortlist, no model: the question's content words are matched (whole
   words, normalised as the citation check normalises) against every chapter
   in the window; chapters score by how many distinct question words they
   hold. Top ``SHORTLIST`` by score, ties to the chapter nearer the position.
2. One small call (skipped below ``MIN_CHAPTERS_FOR_PICK`` chapters, or when
   there are no chapter summaries yet): the question, the window's chapter
   summaries and the shortlist; the model names up to ``MAX_CHAPTERS``
   chapters to read. Refs outside the window are dropped. The union with the
   top ``TOP_TEXT`` text matches, capped at ``MAX_CHAPTERS`` chapters and
   ``ASK_MAX_WORDS`` words (lowest text score dropped first).
   A pick call that fails (refused, cut off, provider error) falls back to
   the text shortlist.
3. One answer call with those chapters' text, each in its own block (not
   marked for caching: a second question within five minutes rarely reads
   the same chapters), then the question. Every quote must
   occur in the text of the chapter it names (``digest.evidence_in_text``);
   quotes that fail are dropped, and an answer left without a quote is
   "not found". Never cached: questions differ.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.models.book import Book, BookChapter
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, settings as ai_settings
from backend.services.ai.provider import (
    AIBudgetExceeded, AIDisabled, AIError, AIKeyInvalid, AINotConfigured,
)

FEATURE = "ask"
MAX_QUESTION_CHARS = 300
SHORTLIST = 12
TOP_TEXT = 3
MAX_CHAPTERS = 8
MIN_CHAPTERS_FOR_PICK = 4
ASK_MAX_WORDS = 40_000
MAX_ANSWER_CHARS = 1200
PICK_MAX_TOKENS = 3000
ANSWER_MAX_TOKENS = 8000
NOT_FOUND_MESSAGE = "Nothing in the chapters you have read answers that."

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each else ever
few for from further get got had has have having he her here hers herself him himself his how
i if in into is it its itself just let me more most my myself no nor not now of off on once
only or other our ours ourselves out over own really said same say says she should so some
still such tell than that the their theirs them themselves then there these they this those
through to too under until up upon very was we were what when where which while who whom whose
why will with would you your yours yourself yourselves happen happened happens know remember
mean meant did does chapter chapters book story thing things something someone anyone
""".split())


class BadQuestion(ValueError):
    pass


# Errors a fallback would only meet again: they end the question.
_NO_FALLBACK = (AIBudgetExceeded, AINotConfigured, AIDisabled, AIKeyInvalid)


def _guess(db: Session, content: list[dict[str, Any]], max_tokens: int) -> float:
    """Spend-limit guess for one Ask call: its output at ``max_tokens``."""
    from backend.services.ai import estimate
    return estimate.content_cost_guess(FEATURE, ai_settings.effective_model(db, FEATURE),
                                       content, max_tokens)


def cost_hint_usd(db: Session) -> float:
    """What one question can cost at most, about: the answer call reading
    ``ASK_MAX_WORDS`` words with its full ``ANSWER_MAX_TOKENS`` on the
    effective model, rounded to cents (the page's "up to about X")."""
    from backend.services.ai import estimate
    return round(estimate.call_cost_guess(FEATURE, ai_settings.effective_model(db, FEATURE),
                                          ASK_MAX_WORDS, max_tokens=ANSWER_MAX_TOKENS,
                                          cached_words=0), 2)


def clean_question(question: str) -> str:
    q = " ".join((question or "").split())
    if not q or not re.search(r"\w", q):
        raise BadQuestion("Type a question about what you have read.")
    if len(q) > MAX_QUESTION_CHARS:
        raise BadQuestion(f"Keep the question under {MAX_QUESTION_CHARS} characters.")
    return q


def content_words(question: str) -> list[str]:
    """Distinct normalised content words of the question, in order: no
    stopwords; words of three or more letters, or shorter ones written with a
    capital (a name like "Al"). A possessive is dropped ("Tobin's" -> tobin)."""
    out: list[str] = []
    for m in _WORD_RE.finditer(question or ""):
        raw = re.sub(r"['’]s$", "", m.group(0), flags=re.IGNORECASE)
        w = digest.normalize_text(raw)
        if not w or w in _STOPWORDS:
            continue
        if len(w) < 3 and not (raw[:1].isupper() and len(w) >= 2):
            continue
        if w not in out:
            out.append(w)
    return out


@dataclass
class WindowChapter:
    book: Book
    chapter: BookChapter
    text: str
    order: int                  # position in reading order (later = nearer the reader)
    current: bool = False       # the position chapter, cut at the position
    score: int = 0
    _norm: str | None = None

    @property
    def ref(self) -> tuple[int, int]:
        return (int(self.book.id), int(self.chapter.idx))

    @property
    def normalized(self) -> str:
        if self._norm is None:
            self._norm = digest.normalize_text(self.text)
        return self._norm

    @property
    def words(self) -> int:
        return book_text.count_words(self.text)


def _volume_label(book: Book) -> str | None:
    idx = book.series_index
    if idx is None:
        return None
    f = float(idx)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def heading(wc: WindowChapter) -> str:
    """``=== [c7] vol 2 · Title ===`` (no ``vol`` part for a book outside a
    numbered series)."""
    title = " ".join((wc.chapter.title or "").split())
    vol = _volume_label(wc.book)
    label = f"[c{digest.chapter_number(wc.chapter)}]" + (f" vol {vol}" if vol is not None else "")
    return f"=== {label} · {title} ===" if title else f"=== {label} ==="


def _book_texts(book: Book, db: Session) -> dict[int, str]:
    chapters = book_text.book_chapters(book, db)
    texts = book_text.chapter_texts(book, db) or []
    return {c.idx: (texts[i] if i < len(texts) else "") or "" for i, c in enumerate(chapters)}


def window_chapters(db: Session, scope: digest.Scope, book: Book,
                    window: companion.Window) -> list[WindowChapter]:
    """Every chapter the reader may be asked about, in reading order: the read
    earlier volumes in full, this book's chapters before the position chapter,
    then the position chapter cut at the position."""
    pairs = digest.scope_chapters(db, scope, window.chapter_idx)
    texts: dict[int, dict[int, str]] = {}
    out: list[WindowChapter] = []
    for b, c in pairs:
        if b.id not in texts:
            texts[b.id] = _book_texts(b, db)
        text = texts[b.id].get(c.idx, "")
        if text.strip():
            out.append(WindowChapter(book=b, chapter=c, text=text, order=len(out)))
    cur = window.chapter
    if cur is not None:
        text = companion.window_text(book, window, db)
        if text.strip():
            out.append(WindowChapter(book=book, chapter=cur, text=text, order=len(out), current=True))
    return out


def score_chapters(chapters: list[WindowChapter], words: list[str]) -> None:
    for wc in chapters:
        wc.score = sum(1 for w in words if digest._find_word(w, wc.normalized))


def ranked(chapters: list[WindowChapter]) -> list[WindowChapter]:
    """By text score, ties to the chapter nearer the position."""
    return sorted(chapters, key=lambda wc: (-wc.score, -wc.order))


# ── step 1b: the pick call ───────────────────────────────────────────────────

PICK_SYSTEM_PROMPT = """\
You help a spoiler-safe reading companion find where a book answers a \
reader's question. You receive the question, one-paragraph summaries of the \
chapters the reader has read (identified by book_id and chapter_idx), and a \
shortlist of chapters whose text holds words from the question.

Return up to 8 chapters whose full text most likely answers the question, \
most likely first, as {book_id, chapter_idx} pairs taken only from the \
summaries or the shortlist. Pick from the summaries' content, not from what \
you may know about the book. If nothing looks relevant, return the chapters \
nearest the end of the list. Return only the JSON the schema describes."""


def pick_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}, "chapter_idx": {"type": "integer"}},
                    "required": ["book_id", "chapter_idx"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["chapters"],
        "additionalProperties": False,
    }


def _ref_dict(wc: WindowChapter) -> dict[str, Any]:
    return {"book_id": wc.book.id, "volume": _volume_label(wc.book),
            "chapter_idx": wc.chapter.idx, "chapter": digest.chapter_number(wc.chapter),
            "title": wc.chapter.title}


def _pick(db: Session, user: User, book: Book, question: str, chapters: list[WindowChapter],
          shortlist: list[WindowChapter], summaries: dict[tuple[int, int], str]) -> list[tuple[int, int]]:
    """Refs the model names, in its order, only those inside the window."""
    listed = [{**_ref_dict(wc), "summary": summaries[wc.ref]}
              for wc in chapters if summaries.get(wc.ref)]
    context = {
        "question": question,
        "chapter_summaries": listed,
        "text_matches": [{**_ref_dict(wc), "matching_question_words": wc.score} for wc in shortlist],
    }
    content = [{"type": "text", "text": "Pick the chapters to read. Context as JSON:\n\n"
                + json.dumps(context, ensure_ascii=False, indent=1)}]
    result = ai.run_feature(db, user, FEATURE, system=PICK_SYSTEM_PROMPT, user_content=content,
                            schema=pick_schema(), max_tokens=PICK_MAX_TOKENS, book_id=book.id,
                            cost_guess=_guess(db, content, PICK_MAX_TOKENS))
    inside = {wc.ref for wc in chapters}
    out: list[tuple[int, int]] = []
    for item in ((result.parsed or {}).get("chapters") or []):
        if not isinstance(item, dict):
            continue
        try:
            ref = (int(item.get("book_id")), int(item.get("chapter_idx")))
        except (TypeError, ValueError):
            continue
        if ref in inside and ref not in out:
            out.append(ref)
        if len(out) >= MAX_CHAPTERS:
            break
    return out


def select(chapters: list[WindowChapter], picked: list[tuple[int, int]]) -> list[WindowChapter]:
    """The model's picks plus the top text matches, at most ``MAX_CHAPTERS``
    chapters and ``ASK_MAX_WORDS`` words, the lowest text score dropped
    first; returned in reading order."""
    order = ranked(chapters)
    wanted = {wc.ref for wc in order[:TOP_TEXT]} | set(picked)
    pick_rank = {ref: i for i, ref in enumerate(picked)}
    cands = sorted((wc for wc in chapters if wc.ref in wanted),
                   key=lambda wc: (-wc.score, pick_rank.get(wc.ref, len(picked)), -wc.order))
    out: list[WindowChapter] = []
    total = 0
    for wc in cands:
        if len(out) >= MAX_CHAPTERS:
            break
        n = wc.words
        if total + n > ASK_MAX_WORDS:
            if out:
                continue
            # One chapter longer than the cap: the part nearest the reader.
            wc.text = book_text.truncate_words(wc.text, ASK_MAX_WORDS,
                                               keep="tail" if wc.current else "head")
            wc._norm = None
            n = wc.words
        out.append(wc)
        total += n
    return sorted(out, key=lambda wc: wc.order)


# ── step 2: the answer ───────────────────────────────────────────────────────

ANSWER_SYSTEM_PROMPT = """\
You answer a reader's question about a book for a spoiler-safe reading \
companion, using only the chapters supplied. They are the chapters the \
reader has read; the last may stop mid-chapter, where the reader stopped.

Return:
- found: true only if the supplied chapters answer the question. When the \
text shows that the answer is "no one", "nothing happened" or "never", that \
is an answer: say so with a quote that shows it, found true. If the chapters \
do not say at all, return found false, answer exactly "The chapters supplied \
do not say." and no quotes.
- answer: 2 to 5 sentences, in the past tense, answering the question from \
the supplied text only.
- quotes: for every claim in the answer, a quote of 3 to 20 words copied \
character for character from one supplied chapter, with that chapter's \
book_id and chapter_idx (listed after the chapters). Never paraphrase a \
quote or join words from different places.

Never use outside knowledge: even if you recognise the book, never use or \
hint at what you know about it from elsewhere, and never mention or hint at \
anything beyond the supplied text, such as what happens later. Plain \
hyphens, never em-dashes. Return only the JSON the schema describes."""


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "answer": {"type": "string"},
            "quotes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "book_id": {"type": "integer"},
                        "chapter_idx": {"type": "integer"},
                        "text": {"type": "string"},
                    },
                    "required": ["book_id", "chapter_idx", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["found", "answer", "quotes"],
        "additionalProperties": False,
    }


def _clean_answer(text: str) -> str:
    t = " ".join((text or "").split())
    t = t.replace(" — ", " - ").replace("—", " - ").replace("–", "-")
    if len(t) > MAX_ANSWER_CHARS:
        cut = t[:MAX_ANSWER_CHARS]
        t = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:") + "..."
    return t


def check_quotes(raw: Any, sent: dict[tuple[int, int], WindowChapter]) -> list[tuple[WindowChapter, str]]:
    out: list[tuple[WindowChapter, str]] = []
    seen: set[tuple[tuple[int, int], str]] = set()
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        try:
            ref = (int(q.get("book_id")), int(q.get("chapter_idx")))
        except (TypeError, ValueError):
            continue
        wc = sent.get(ref)
        text = digest._clean_evidence(str(q.get("text") or ""))
        if wc is None or not digest.evidence_in_text(text, wc.normalized):
            continue
        key = (ref, digest.normalize_text(text))
        if key not in seen:
            seen.add(key)
            out.append((wc, text))
    return out


def _where(wc: WindowChapter) -> dict[str, Any]:
    return {"book_id": wc.book.id, "series_index": wc.book.series_index,
            "chapter_idx": wc.chapter.idx, "chapter_title": wc.chapter.title}


def ask(db: Session, user: User, book: Book, window: companion.Window, question: str) -> dict[str, Any]:
    """``{answer, found, quotes, chapters_read, chapter_idx, chapter_title}``.

    Raises BadQuestion, ``AIDisabled`` when Ask or the digests are off, and
    the typed ``AIError``s of the calls (``AINotConfigured`` without a key,
    ``AIBudgetExceeded`` over a spend limit)."""
    ai.check_feature_available(db, FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    question = clean_question(question)
    with companion.in_flight("ask", user.id, book.id,
                             "A question about this book is already being answered."):
        return _ask(db, user, book, window, question)


def _ask(db: Session, user: User, book: Book, window: companion.Window, question: str) -> dict[str, Any]:
    base: dict[str, Any] = {"chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title}
    not_found = {**base, "answer": NOT_FOUND_MESSAGE, "found": False, "quotes": []}

    scope = digest.user_scope(db, user, book)
    chapters = window_chapters(db, scope, book, window)
    if not chapters:
        return {**not_found, "chapters_read": []}
    score_chapters(chapters, content_words(question))

    if len(chapters) < MIN_CHAPTERS_FOR_PICK:
        chosen = select(chapters, [wc.ref for wc in chapters])
    else:
        summaries = {(int(d.book_id), int(d.chapter_idx)): (d.summary or "").strip()
                     for d in digest.scoped_digests(db, scope, window.chapter_idx)
                     if d.model not in (digest.NO_MODEL, digest.FAILED_MODEL)}
        shortlist = ranked(chapters)[:SHORTLIST]
        picked: list[tuple[int, int]] = [wc.ref for wc in shortlist]
        if any(summaries.values()):
            try:
                picked = _pick(db, user, book, question, chapters, shortlist, summaries)
            except _NO_FALLBACK:
                raise
            except AIError:
                # Refused, cut off or a provider error: read the text
                # shortlist instead (the answer call is what matters).
                picked = [wc.ref for wc in shortlist]
        chosen = select(chapters, picked)

    sent = {wc.ref: wc for wc in chosen}
    # The chapter text goes first, then the question. Not marked for
    # caching: the cache write costs more than plain input and a second
    # question within five minutes rarely reads the same chapters.
    content: list[dict[str, Any]] = [{"type": "text", "text": f"{heading(wc)}\n\n{wc.text}"}
                                     for wc in chosen]
    context = {
        "question": question,
        "chapters": [{**_ref_dict(wc), "heading": heading(wc),
                      "ends_at_reader_position": wc.current} for wc in chosen],
    }
    content.append({"type": "text", "text": "The blocks above are the chapters to answer from. "
                                            "Answer this question. Context as JSON:\n\n"
                                            + json.dumps(context, ensure_ascii=False, indent=1)})
    result = ai.run_feature(db, user, FEATURE, system=ANSWER_SYSTEM_PROMPT, user_content=content,
                            schema=answer_schema(), max_tokens=ANSWER_MAX_TOKENS, book_id=book.id,
                            cost_guess=_guess(db, content, ANSWER_MAX_TOKENS))
    parsed = result.parsed or {}
    chapters_read = [_where(wc) for wc in chosen]
    quotes = check_quotes(parsed.get("quotes"), sent) if parsed.get("found") is True else []
    answer = _clean_answer(str(parsed.get("answer") or ""))
    if not quotes or not answer:
        return {**not_found, "chapters_read": chapters_read}
    return {
        **base,
        "answer": answer,
        "found": True,
        "quotes": [{**_where(wc), "text": text} for wc, text in quotes],
        "chapters_read": chapters_read,
    }
