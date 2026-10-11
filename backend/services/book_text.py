"""Full-book text for the reading companion, split into chapters that line up
with the stored ``BookChapter`` fractions.

A chapter map stores boundaries as fraction-of-book by word offset (see
``metadata._build_chapter_map``). To hand the model exactly the text of
chapter k, the book is re-read through the SAME documents and the SAME
tokenisation the map was computed from (``metadata.epub_chapter_documents``:
ebooklib's spine first, the zip-level parser when ebooklib gives no map; words
counted by ``metadata._WORD_RE`` over ``metadata._html_to_text``'s output).
Chapter k is then the words ``[round(start * total), round(end * total))`` of
the whole book. When the map came from this file, those offsets are exactly
the spine-document boundaries; when the stored map is older than the file the
split still follows the stored fractions, so a chapter can never reach past
the boundary the rest of Tome uses.

Display text keeps paragraph breaks: tags are replaced by a newline (block
tags) or a space (inline tags) in one pass over the same tag spans
``_html_to_text`` uses, so the word sequence is identical to the counted one.

EPUB only. PDF and comics have no chapter map; ``unavailable_reason`` says so.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session, object_session

from backend.models.book import Book, BookChapter
from backend.services.metadata import _WORD_RE, count_words_text, epub_chapter_documents

logger = logging.getLogger(__name__)

NOT_EPUB = "not_epub"
NO_CHAPTERS = "no_chapters"

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_BLOCK_TAG_RE = re.compile(
    r"(?is)^</?(?:p|div|h[1-6]|br|li|ul|ol|tr|td|th|table|blockquote|section|article|header|"
    r"footer|aside|pre|hr|dd|dt|dl|figure|figcaption|title|body|head|html|nav)\b"
)
_FRACTION_EPS = 1e-9


# ── text extraction ──────────────────────────────────────────────────────────

def html_to_display_text(raw: str) -> str:
    """Tags out, paragraph breaks kept. Same tag spans as
    ``metadata._html_to_text`` (script/style blocks, then every ``<...>``), each
    replaced by whitespace, so ``_WORD_RE`` finds the same words in the same
    order. Entities are left escaped here (as in the word count) and decoded
    when a slice is cleaned for display."""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    return _TAG_RE.sub(lambda m: "\n" if _BLOCK_TAG_RE.match(m.group(0)) else " ", text)


def _clean(segment: str) -> str:
    segment = html_lib.unescape(segment).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in segment.split("\n"))
    return "\n".join(line for line in lines if line)


@dataclass(frozen=True)
class _Doc:
    text: str                    # display text, entities still escaped
    word_starts: tuple[int, ...]  # char offset of each counted word
    raw: str = ""                # the document's HTML (for locator lookups)


@dataclass(frozen=True)
class BookDocs:
    docs: tuple[_Doc, ...]
    cum: tuple[int, ...]          # cum[i] = words before doc i; cum[-1] = total
    file_map: tuple[float, ...]   # start fractions of the map this file yields

    @property
    def total(self) -> int:
        return self.cum[-1]

    def _locate(self, word: int) -> tuple[int, int]:
        """(doc index, char offset) where global word ``word`` starts. A word
        at a document's first word starts at char 0 of that document."""
        for i, doc in enumerate(self.docs):
            if self.cum[i] <= word < self.cum[i + 1]:
                local = word - self.cum[i]
                return i, (0 if local == 0 else doc.word_starts[local])
        return len(self.docs), 0

    def text_between(self, start_word: int, end_word: int) -> str:
        """Clean display text of words ``[start_word, end_word)``."""
        total = self.total
        start_word = max(0, min(start_word, total))
        end_word = max(start_word, min(end_word, total))
        if end_word <= start_word:
            return ""
        d0, c0 = self._locate(start_word)
        d1, c1 = self._locate(end_word) if end_word < total else (len(self.docs), 0)
        parts: list[str] = []
        for i in range(d0, min(d1 + 1, len(self.docs))):
            text = self.docs[i].text
            lo = c0 if i == d0 else 0
            hi = c1 if i == d1 else len(text)
            piece = _clean(text[lo:hi])
            if piece:
                parts.append(piece)
        return "\n\n".join(parts)


def _docs_from_htmls(htmls: list[str]) -> list[_Doc]:
    out: list[_Doc] = []
    for raw in htmls:
        text = html_to_display_text(raw)
        out.append(_Doc(text=text, word_starts=tuple(m.start() for m in _WORD_RE.finditer(text)),
                        raw=raw))
    return out


@lru_cache(maxsize=4)
def _load(path_str: str, _mtime_ns: int, _size: int) -> BookDocs:
    """Parsed once per file version (path + mtime + size key the cache)."""
    htmls, chapters = epub_chapter_documents(Path(path_str))
    docs = _docs_from_htmls(htmls)
    cum = [0]
    for d in docs:
        cum.append(cum[-1] + len(d.word_starts))
    return BookDocs(
        docs=tuple(docs),
        cum=tuple(cum),
        file_map=tuple(float(c["start_fraction"]) for c in chapters),
    )


def load_book_docs(path: Path) -> BookDocs:
    st = path.stat()
    return _load(str(path), st.st_mtime_ns, st.st_size)


def spine_texts(path: Path) -> list[str]:
    """Full-book text per spine document, in reading order (cleaned)."""
    docs = load_book_docs(path)
    return [_clean(d.text) for d in docs.docs]


# ── book-level helpers ───────────────────────────────────────────────────────

def _session(book: Book, db: Session | None) -> Session:
    s = db or object_session(book)
    if s is None:
        raise ValueError("book is not attached to a session; pass db")
    return s


def epub_path(book: Book) -> Path | None:
    """The book's EPUB file on disk, or None."""
    for f in book.files or []:
        if (f.format or "").lower() != "epub":
            continue
        path = Path(f.file_path)
        if path.is_file():
            return path
    return None


def has_epub_file(book: Book) -> bool:
    return any((f.format or "").lower() == "epub" for f in (book.files or []))


def book_chapters(book: Book, db: Session | None = None) -> list[BookChapter]:
    return (
        _session(book, db).query(BookChapter)
        .filter(BookChapter.book_id == book.id)
        .order_by(BookChapter.idx)
        .all()
    )


def unavailable_reason(book: Book, db: Session | None = None) -> str | None:
    """``"not_epub"`` | ``"no_chapters"`` | None when chapter text is available.
    A missing file on disk counts as ``not_epub`` (there is no text to read)."""
    if not has_epub_file(book):
        return NOT_EPUB
    if not book_chapters(book, db):
        return NO_CHAPTERS
    if epub_path(book) is None:
        return NOT_EPUB
    return None


def chapter_for_fraction(book: Book, pct: float | None, db: Session | None = None) -> int | None:
    """The chapter the position ``pct`` (0..1) falls in: the last chapter whose
    ``start_fraction <= pct`` (rounding DOWN). None without a chapter map, for
    ``pct`` None, or before chapter 0."""
    if pct is None:
        return None
    found: int | None = None
    for ch in book_chapters(book, db):
        if ch.start_fraction <= pct + _FRACTION_EPS:
            found = ch.idx
        else:
            break
    return found


def _bounds(docs: BookDocs, ch: BookChapter) -> tuple[int, int]:
    total = docs.total
    start = int(round(ch.start_fraction * total))
    end = total if ch.end_fraction >= 1.0 - _FRACTION_EPS else int(round(ch.end_fraction * total))
    return start, end


def _aligned(docs: BookDocs, chapters: list[BookChapter]) -> bool:
    if len(docs.file_map) != len(chapters):
        return False
    return all(abs(a - c.start_fraction) < 1e-6 for a, c in zip(docs.file_map, chapters))


def chapter_text(book: Book, idx: int, db: Session | None = None, *,
                 until: float | None = None) -> str | None:
    """Plain text of chapter ``idx`` (paragraphs separated by newlines).

    ``until`` (a book fraction) cuts the text at that word offset when it
    falls inside the chapter, for a window that ends exactly at the reader's
    position. None when the book is not an EPUB on disk, has no chapter map,
    or ``idx`` is out of range."""
    chapters = book_chapters(book, db)
    ch = next((c for c in chapters if c.idx == idx), None)
    if ch is None:
        return None
    path = epub_path(book)
    if path is None:
        return None
    try:
        docs = load_book_docs(path)
    except Exception as exc:  # noqa: BLE001 - unreadable file: no text
        logger.info("book_text: could not read book %s (%s)", book.id, exc)
        return None
    if docs.total <= 0:
        return None
    if not _aligned(docs, chapters):
        logger.info("book_text: stored chapter map of book %s differs from the file's; "
                    "splitting by the stored fractions", book.id)
    start, end = _bounds(docs, ch)
    if until is not None:
        end = min(end, max(start, int(round(until * docs.total))))
    return docs.text_between(start, end)


def chapter_texts(book: Book, db: Session | None = None) -> list[str] | None:
    """Every chapter's text, in ``idx`` order (None as for ``chapter_text``)."""
    chapters = book_chapters(book, db)
    if not chapters:
        return None
    path = epub_path(book)
    if path is None:
        return None
    try:
        docs = load_book_docs(path)
    except Exception as exc:  # noqa: BLE001
        logger.info("book_text: could not read book %s (%s)", book.id, exc)
        return None
    return [docs.text_between(*_bounds(docs, ch)) for ch in chapters]


def count_words(text: str) -> int:
    """Words as the chapter map counts them."""
    return count_words_text(text)


def truncate_words(text: str, max_words: int, *, keep: str = "head") -> str:
    """At most ``max_words`` counted words of ``text``: the first ones
    (``keep="head"``) or the last ones (``keep="tail"``), cut on word starts so
    the result is still a verbatim slice."""
    starts = [m.start() for m in _WORD_RE.finditer(text)]
    if len(starts) <= max_words:
        return text
    if keep == "tail":
        return text[starts[len(starts) - max_words]:]
    return text[:starts[max_words]].rstrip()


# ── the reader's position in word units ─────────────────────────────────────
#
# The chapter map and ``chapter_text(until=)`` count WORDS. The position a
# reader syncs does not: KOReader sends ``getLastPercent()`` (rendered pages
# over page count) and the web reader sends foliate's section-size fraction.
# Pages without words (cover, colour plates, title page, the half-empty last
# page of every chapter) make that fraction run AHEAD of the word fraction,
# so using it as-is would put the cut, and the chapter rounding, past the
# reader. ``reading_fraction`` converts it:
#
# - With a locator (KOReader xpointer ``/body/DocFragment[N]/body/...``, or
#   the web reader's ``epubcfi(/6/2N!/4/...)``), the spine document is known
#   exactly and the element path is resolved to the word it starts at: the
#   top of the reader's page, never past it. When only the document resolves,
#   the position is kept inside that document and backed off by the margin.
# - Without a usable locator, the fraction is backed off by ``POSITION_MARGIN``
#   of the book (best effort; a locator is what makes it exact).

POSITION_MARGIN = 0.03

_XPOINTER_RE = re.compile(r"^/body/DocFragment\[(\d+)\](.*)$", re.S)
_XP_STEP_RE = re.compile(r"^([A-Za-z_][\w.:-]*?)(?:\[(\d+)\])?(?:\.\d+)?$")
_CFI_RE = re.compile(r"^epubcfi\((.*)\)$", re.S)
_CFI_STEP_RE = re.compile(r"/(\d+)(?:\[(?:\^.|[^\]])*\])?")
_MARKUP_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[!?][^>]*>"
    r"|<(/?)([A-Za-z][^\s/>]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.S,
)
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                        "meta", "param", "source", "track", "wbr"})


@dataclass(frozen=True)
class Locator:
    """A reading position inside one spine document: ``doc`` is the 0-based
    spine index, ``steps`` the element path below ``<html>``. A step is
    ``(name, n)`` for an xpointer (n-th child element of that name, 1-based)
    or ``(None, n)`` for a CFI (n-th child element, 1-based)."""
    doc: int
    steps: tuple[tuple[str | None, int], ...]


def _local_name(name: str) -> str:
    return name.rsplit(":", 1)[-1].lower()


def parse_locator(progress: str | None) -> Locator | None:
    """A KOReader xpointer or a web-reader EPUB CFI, else None."""
    s = (progress or "").strip()
    m = _XPOINTER_RE.match(s)
    if m:
        doc = int(m.group(1)) - 1
        if doc < 0:
            return None
        xp_steps: list[tuple[str | None, int]] = []
        for seg in m.group(2).split("/"):
            if not seg:
                continue
            if seg.startswith("text()") or seg.startswith("comment()"):
                break
            sm = _XP_STEP_RE.match(seg)
            if sm is None:
                break
            xp_steps.append((_local_name(sm.group(1)), int(sm.group(2) or 1)))
        return Locator(doc=doc, steps=tuple(xp_steps))
    m = _CFI_RE.match(s)
    if not m:
        return None
    # A range CFI is "parent,start,end": the reader's page starts at parent+start.
    parts = re.split(r"(?<!\^),", m.group(1))
    path = parts[0] + (parts[1] if len(parts) > 2 else "")
    if "!" not in path:
        return None
    package, content = path.split("!", 1)
    spine_steps = [int(x) for x in _CFI_STEP_RE.findall(package)]
    if len(spine_steps) < 2 or spine_steps[-1] % 2 or spine_steps[-1] < 2:
        return None
    cfi_steps: list[tuple[str | None, int]] = []
    for n in (int(x) for x in _CFI_STEP_RE.findall(content)):
        if n % 2 or n < 2:
            break          # an odd step is a text node: stop at its element
        cfi_steps.append((None, n // 2))
    return Locator(doc=spine_steps[-1] // 2 - 1, steps=tuple(cfi_steps))


def _element_offset(raw: str, steps: tuple[tuple[str | None, int], ...]) -> tuple[int | None, bool]:
    """(char offset in ``raw`` where the deepest element of ``steps`` that
    exists starts, whether the whole path matched). The path starts below the
    ``<html>`` element. (None, False) when not even the first step matched."""
    if not steps:
        return None, False
    # Each frame: [name, on_path depth or -1, per-name counts, element count]
    stack: list[list] = []
    best: int | None = None
    pos = 0
    in_html = False
    while True:
        m = _MARKUP_RE.search(raw, pos)
        if m is None:
            break
        pos = m.end()
        name = m.group(2)
        if name is None:
            continue                                   # comment, doctype, PI, CDATA
        name = _local_name(name)
        if m.group(1):                                 # end tag
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == name:
                    left = stack[i:]
                    del stack[i:]
                    if any(f[1] >= 0 for f in left) and best is not None:
                        return best, False             # left the path: the rest does not exist
                    break
            continue
        self_closing = (m.group(3) or "").rstrip().endswith("/") or name in _VOID_TAGS
        if not in_html:
            if name == "html":
                in_html = True
                if not self_closing:
                    stack.append([name, 0, {}, 0])
            continue
        if not stack:
            continue
        parent = stack[-1]
        parent[3] += 1
        parent[2][name] = parent[2].get(name, 0) + 1
        depth = -1
        if 0 <= parent[1] < len(steps):
            want_name, want_n = steps[parent[1]]
            hit = parent[3] == want_n if want_name is None else (
                name == want_name and parent[2][name] == want_n)
            if hit:
                depth = parent[1] + 1
                best = m.start()
                if depth == len(steps):
                    return best, True
        if name in ("script", "style") and not self_closing:
            end = re.search(rf"</{name}\s*>", raw[pos:], re.I)
            pos = pos + end.end() if end else len(raw)
            continue
        if not self_closing:
            stack.append([name, depth, {}, 0])
    return best, False


def _words_before(raw: str, offset: int) -> int:
    """Counted words of ``raw[:offset]`` (``offset`` is a tag start, so no
    word straddles it)."""
    return len(_WORD_RE.findall(html_to_display_text(raw[:offset])))


def word_position(docs: BookDocs, pct: float, locator: Locator | None) -> int:
    """The reader's position as a global word offset (see the section note)."""
    total = docs.total
    raw_word = int(round(pct * total))
    margin = int(round(POSITION_MARGIN * total))
    if locator is not None and 0 <= locator.doc < len(docs.docs):
        lo, hi = docs.cum[locator.doc], docs.cum[locator.doc + 1]
        doc = docs.docs[locator.doc]
        offset, exact = _element_offset(doc.raw, locator.steps) if doc.raw else (None, False)
        start = lo + _words_before(doc.raw, offset) if offset is not None else lo
        start = min(start, hi)
        if exact:
            # Trust the locator, but never far past what the fraction says
            # (a spine numbered differently from ours must not run ahead).
            return max(0, min(start, raw_word + margin))
        # Inside the document: at least where the resolved part of the path
        # starts, at most the end of the document.
        return max(start, min(raw_word - margin, hi))
    return max(0, raw_word - margin)


def reading_fraction(book: Book, pct: float | None, locator: str | None = None,
                     db: Session | None = None) -> float | None:
    """The reader's position ``pct`` (a page or section fraction from a
    device or the web reader) as a WORD fraction, the unit of the chapter
    map, never past where the reader is. ``locator`` is the stored xpointer
    or CFI. A finished book (``pct`` >= 1) stays 1.0. None for ``pct`` None."""
    if pct is None:
        return None
    pct = max(0.0, min(1.0, float(pct)))
    if pct >= 1.0 - _FRACTION_EPS or pct <= 0.0:
        return pct
    docs = None
    path = epub_path(book)
    if path is not None:
        try:
            docs = load_book_docs(path)
        except Exception as exc:  # noqa: BLE001 - unreadable file: margin only
            logger.info("book_text: could not read book %s (%s)", book.id, exc)
    if docs is None or docs.total <= 0:
        return max(0.0, pct - POSITION_MARGIN)
    return word_position(docs, pct, parse_locator(locator)) / docs.total
