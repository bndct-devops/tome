"""
filename_parser.py — Parse book filenames to extract series metadata.

Detects content_type (chapter vs volume), series name, series_index, and
author from common ebook/manga/comic filename conventions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class ParsedFilename:
    title: str               # cleaned title (e.g. "War and Peace Chapter 12" or "Beowulf")
    series: str | None       # detected series name (e.g. "War and Peace")
    series_index: float | None  # detected number (e.g. 1179.0 or 18.0)
    content_type: str        # "chapter" or "volume"
    author: str | None = None  # detected author (e.g. "Eric Ugland")


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# File extensions to strip
_RE_EXT = re.compile(r"\.(cbz|cbr|epub|pdf|mobi|azw3|zip)$", re.IGNORECASE)

# Extensions where a bare trailing number does NOT imply a manga chapter —
# for prose ebooks "Title 5" is almost always volume 5 of something, and the
# something is NOT the title, so fabricating series=title is wrong.
_EBOOK_EXTS = {"epub", "pdf", "mobi", "azw3"}

# Parenthesised segments: (Digital), (2026), (1r0n), etc.
_RE_PAREN = re.compile(r"\([^)]*\)")

# Bracketed tags: [CBZ], [1r0n], etc.
_RE_BRACKET = re.compile(r"\[[^\]]*\]")

# Series parenthetical, the Amazon/Calibre convention:
# "(The Bad Guys Book 8)", "(Cradle, Book 3)", "(Wandering Inn #7)"
_RE_PAREN_SERIES = re.compile(
    r"\(\s*([^()]+?)\s*,?\s+(?:book|bk\.?|no\.?|#)\s*(\d+(?:\.\d+)?)\s*\)",
    re.IGNORECASE,
)

# Import-script layout: "NN. Title - Author (Year)" / "Title - Author (Year)"
_RE_TITLE_AUTHOR_YEAR = re.compile(
    r"^(?:(\d{1,3})\.\s+)?(.+?)\s+-\s+([^\-()\[\]]+?)\s*\(\d{4}\)\s*$"
)

# Same layout without the year — only trusted with the leading index, which
# fixes the orientation (title first, author last).
_RE_INDEX_TITLE_AUTHOR = re.compile(
    r"^(\d{1,3})\.\s+(.+?)\s+-\s+([^\-()\[\]]+?)\s*$"
)

# Chapter indicators: Chapter 1134, Ch.230, Ch 230
_RE_CHAPTER_KEYWORD = re.compile(
    r"\b(?:chapter|ch)\.?\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# A chapter range: "Ch 1-7", "Ch.1 - 10", "Chapter 1~7". After a volume marker
# it says which chapters the volume holds, so the file is the volume.
_RE_CHAPTER_RANGE = re.compile(
    r"\b(?:chapter|ch)\.?\s*\d+(?:\.\d+)?\s*[-–~]\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)

# Volume indicators: v01, v001, v18, Vol.01, Vol 01, Volume 01, Volume.01
_RE_VOLUME = re.compile(
    r"\bv(?:ol(?:ume)?\.?\s*)?(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Bare trailing number: "Series Name 1179" → captures series + number
_RE_BARE_NUMBER = re.compile(
    r"^(.*?)\s+(\d+(?:\.\d+)?)\s*$"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_extension(filename: str) -> str:
    return _RE_EXT.sub("", filename).strip()


def _extension(filename: str) -> str | None:
    m = _RE_EXT.search(filename)
    return m.group(1).lower() if m else None


def _strip_noise(text: str) -> str:
    """Remove parenthesised metadata and bracketed tags."""
    text = _RE_PAREN.sub(" ", text)
    text = _RE_BRACKET.sub(" ", text)
    # Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _normalise_number(raw: str) -> float:
    return float(raw)


def _clean_series(raw: str) -> str | None:
    """Strip trailing punctuation/whitespace from a candidate series name."""
    cleaned = raw.strip().rstrip(" -–,.")
    return cleaned if cleaned else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_filename(filename: str, in_chapters_dir: bool = False) -> ParsedFilename:
    """Parse a book filename to extract metadata.

    Args:
        filename: The filename (with or without extension).
        in_chapters_dir: If True, force content_type to "chapter" regardless
                         of what the filename itself implies (file lives in a
                         chapters/ subfolder).

    Returns:
        ParsedFilename with title, series, series_index, content_type, author.
    """
    ext = _extension(filename)

    # 1. Strip extension
    work = _strip_extension(filename)

    author: str | None = None
    series: str | None = None
    series_index: float | None = None

    # 2. Series parenthetical — must run BEFORE noise-stripping, which would
    # otherwise destroy the one place the real series name lives.
    ps = _RE_PAREN_SERIES.search(work)
    if ps:
        series = _clean_series(ps.group(1))
        series_index = _normalise_number(ps.group(2))
        work = (work[: ps.start()] + " " + work[ps.end():]).strip()

    # 3. Structured "NN. Title - Author (Year)" layout (the organizer/import
    # convention). The trailing (Year) or leading index anchors which side is
    # the author.
    sm = _RE_TITLE_AUTHOR_YEAR.match(work) or _RE_INDEX_TITLE_AUTHOR.match(work)
    if sm:
        leading_idx, title_part, author_part = sm.group(1), sm.group(2), sm.group(3)
        author = author_part.strip() or None
        work = title_part
        if series_index is None and leading_idx is not None:
            series_index = _normalise_number(leading_idx)

    # Keep a "display title" from the stripped (but not noise-cleaned) name
    # so we can build a reasonable title string later.
    display_base = work

    # 4. Strip parenthesised metadata and bracketed tags
    work = _strip_noise(work)

    # 5. Chapter detection (must come BEFORE volume)
    chapter_match = _RE_CHAPTER_KEYWORD.search(work)
    if chapter_match:
        # "Series Vol 1 Ch 1-7": a volume named with the chapters it holds.
        range_match = _RE_CHAPTER_RANGE.match(work, chapter_match.start())
        volume_before = _RE_VOLUME.search(work[: chapter_match.start()])
        if range_match and volume_before:
            work = (work[: range_match.start()] + " " + work[range_match.end():]).strip()
            chapter_match = None
    if chapter_match:
        num = _normalise_number(chapter_match.group(1))
        detected = _clean_series(work[: chapter_match.start()])
        series = series or detected

        # Build a clean title: series + "Chapter N"
        title_parts = []
        if detected:
            title_parts.append(detected)
        title_parts.append(f"Chapter {_fmt_number(num)}")
        title = " ".join(title_parts)

        return ParsedFilename(
            title=title,
            series=series,
            series_index=series_index if series_index is not None else num,
            content_type="chapter",
            author=author,
        )

    # 6. Volume detection
    volume_match = _RE_VOLUME.search(work)
    if volume_match:
        num = _normalise_number(volume_match.group(1))
        detected = _clean_series(work[: volume_match.start()])
        series = series or detected

        title_parts = []
        if detected:
            title_parts.append(detected)
        title_parts.append(f"v{_fmt_number(num)}")
        title = " ".join(title_parts)

        content_type = "chapter" if in_chapters_dir else "volume"
        return ParsedFilename(
            title=title,
            series=series,
            series_index=series_index if series_index is not None else num,
            content_type=content_type,
            author=author,
        )

    # 7. Bare trailing number. For comics this is the standard chapter
    # convention ("One Piece 1134.cbz"). For prose ebooks it is NOT a series
    # signal — "Dukes and Ladders 5.epub" is volume 5 of some series the
    # filename doesn't name, and deriving series=title poisons real metadata
    # downstream (the original Bindery series bug).
    bare_match = _RE_BARE_NUMBER.match(work)
    if bare_match:
        num = _normalise_number(bare_match.group(2))

        if ext in _EBOOK_EXTS:
            content_type = "chapter" if in_chapters_dir else "volume"
            return ParsedFilename(
                title=work,
                series=series,
                series_index=series_index if series_index is not None else num,
                content_type=content_type,
                author=author,
            )

        detected = _clean_series(bare_match.group(1))
        series = series or detected

        title_parts = []
        if detected:
            title_parts.append(detected)
        title_parts.append(_fmt_number(num))
        title = " ".join(title_parts)

        return ParsedFilename(
            title=title,
            series=series,
            series_index=series_index if series_index is not None else num,
            content_type="chapter",
            author=author,
        )

    # 8. Fallback: no number detected → volume
    content_type = "chapter" if in_chapters_dir else "volume"
    return ParsedFilename(
        title=work or display_base,
        series=series,
        series_index=series_index,
        content_type=content_type,
        author=author,
    )


def _fmt_number(n: float) -> str:
    """Format a float nicely: 1.0 → '1', 1.5 → '1.5'."""
    return str(int(n)) if n == int(n) else str(n)
