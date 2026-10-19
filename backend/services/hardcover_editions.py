"""Which Hardcover edition stands for a Tome book.

A Hardcover book has many editions: hardcover, paperback, ebook, translations
and the audiobook. They all carry ISBNs, and for popular light novels the
most-used edition is often the audiobook (Podium). Tome has no audio format,
so an audiobook edition is never the right one: its ISBN has no cover in the
catalogues, its pages are missing, and every lookup keyed on it goes wrong.

``pick_edition`` is the one choice used by the metadata fetch
(``metadata_fetch``) and the Hardcover sync (``hardcover_sync``), so both
agree: never audio, the book's language first (English when unknown), then
ebook, then physical, then the edition most people use.

``note_editions`` remembers audiobook ISBNs seen while fetching, so a write
path can refuse one without a network call (``is_known_audio``).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Hardcover reading_format_id values.
PHYSICAL_FORMAT_ID = 1
AUDIO_FORMAT_ID = 2
BOTH_FORMAT_ID = 3   # "physical and ebook" - read as physical
EBOOK_FORMAT_ID = 4

FORMATS = ("audiobook", "ebook", "physical", "unknown")
DEFAULT_LANGUAGE = "en"

# Fields every edition query asks for, so pick_edition has what it needs.
EDITION_FIELDS = """
            id
            isbn_13
            isbn_10
            pages
            users_count
            reading_format_id
            language { code2 }
            publisher { name }
"""

_KNOWN_AUDIO_MAX = 20000
_known_audio: set[str] = set()


def edition_format(ed: dict) -> str:
    """``audiobook`` | ``ebook`` | ``physical`` | ``unknown``."""
    fmt = ed.get("reading_format_id")
    if fmt == AUDIO_FORMAT_ID:
        return "audiobook"
    if fmt == EBOOK_FORMAT_ID:
        return "ebook"
    if fmt in (PHYSICAL_FORMAT_ID, BOTH_FORMAT_ID):
        return "physical"
    return "unknown"


def is_audio(ed: dict) -> bool:
    return ed.get("reading_format_id") == AUDIO_FORMAT_ID


def edition_lang(ed: dict) -> Optional[str]:
    code = ((ed.get("language") or {}).get("code2") or "").strip().lower()
    return code or None


def edition_isbn(ed: dict) -> Optional[str]:
    """The edition's ISBN-13, else its ISBN-10."""
    for key in ("isbn_13", "isbn_10"):
        val = normalize_isbn(ed.get(key))
        if val:
            return val
    return None


def edition_publisher(ed: dict) -> Optional[str]:
    pub = ed.get("publisher")
    name = pub.get("name") if isinstance(pub, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def pick_edition(
    editions: Iterable[dict],
    *,
    language: Optional[str] = None,
    prefer: tuple[str, ...] = ("ebook", "physical"),
    require_isbn: bool = False,
) -> Optional[dict]:
    """The edition that stands for a Tome book, or None.

    Never an audiobook. Ranked by: language match (``language``, English when
    None; an edition of unknown language beats one in another language), then
    format in ``prefer`` order (formats not listed come last), then users,
    then whether it has a page count. ``require_isbn`` skips editions without
    one. Editions without an ``id`` are skipped."""
    want = (language or DEFAULT_LANGUAGE).strip().lower()[:2] or DEFAULT_LANGUAGE

    def score(ed: dict) -> tuple:
        ed_lang = edition_lang(ed)
        if ed_lang == want:
            lang_rank = 2
        elif ed_lang is None:
            lang_rank = 1
        else:
            lang_rank = 0
        fmt = edition_format(ed)
        fmt_rank = len(prefer) - prefer.index(fmt) if fmt in prefer else 0
        return (lang_rank, fmt_rank, ed.get("users_count") or 0, 1 if ed.get("pages") else 0)

    usable = [
        e for e in editions
        if isinstance(e, dict) and e.get("id") is not None and not is_audio(e)
        and (not require_isbn or edition_isbn(e))
    ]
    return max(usable, key=score) if usable else None


# ── ISBN helpers ──────────────────────────────────────────────────────────────

def normalize_isbn(raw: object) -> Optional[str]:
    """Strip separators; a 10/13-character ISBN or None."""
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
    return cleaned if len(cleaned) in (10, 13) else None


def isbn10_to_13(isbn10: str) -> Optional[str]:
    if len(isbn10) != 10:
        return None
    core = "978" + isbn10[:9]
    if not core.isdigit():
        return None
    check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(core)) % 10) % 10
    return core + str(check)


def _forms(isbn: str) -> set[str]:
    out = {isbn}
    if len(isbn) == 10:
        thirteen = isbn10_to_13(isbn)
        if thirteen:
            out.add(thirteen)
    return out


# ── known audiobook ISBNs (in memory, this process) ──────────────────────────

def note_editions(editions: Iterable[dict]) -> None:
    """Remember the ISBNs of the audiobook editions in ``editions``."""
    global _known_audio
    for ed in editions:
        if not isinstance(ed, dict) or not is_audio(ed):
            continue
        for key in ("isbn_13", "isbn_10"):
            val = normalize_isbn(ed.get(key))
            if val:
                if len(_known_audio) >= _KNOWN_AUDIO_MAX:
                    _known_audio = set()
                _known_audio.update(_forms(val))


def note_audio_isbn(isbn: Optional[str]) -> None:
    val = normalize_isbn(isbn)
    if val:
        note_editions([{"reading_format_id": AUDIO_FORMAT_ID, "isbn_13": val}])


def is_known_audio(isbn: Optional[str]) -> bool:
    val = normalize_isbn(isbn)
    return bool(val) and bool(_forms(val) & _known_audio)


def forget_known_audio() -> None:
    """Tests only."""
    _known_audio.clear()
