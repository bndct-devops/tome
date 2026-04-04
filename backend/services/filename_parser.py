"""
filename_parser.py — Parse book filenames to extract series metadata.

Detects content_type (chapter vs volume), series name, and series_index
from common ebook/manga/comic filename conventions.
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


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# File extensions to strip
_RE_EXT = re.compile(r"\.(cbz|cbr|epub|pdf|mobi|azw3|zip)$", re.IGNORECASE)

# Parenthesised segments: (Digital), (2026), (1r0n), etc.
_RE_PAREN = re.compile(r"\([^)]*\)")

# Bracketed tags: [CBZ], [1r0n], etc.
_RE_BRACKET = re.compile(r"\[[^\]]*\]")

# Chapter indicators: Chapter 1134, Ch.230, Ch 230
_RE_CHAPTER_KEYWORD = re.compile(
    r"\b(?:chapter|ch)\.?\s*(\d+(?:\.\d+)?)\b",
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
        ParsedFilename with title, series, series_index, and content_type.
    """
    # 1. Strip extension
    work = _strip_extension(filename)

    # Keep a "display title" from the stripped (but not noise-cleaned) name
    # so we can build a reasonable title string later.
    display_base = work

    # 2 & 3. Strip parenthesised metadata and bracketed tags
    work = _strip_noise(work)

    # 4. Chapter detection (must come BEFORE volume)
    chapter_match = _RE_CHAPTER_KEYWORD.search(work)
    if chapter_match:
        num = _normalise_number(chapter_match.group(1))
        series_raw = work[: chapter_match.start()]
        series = _clean_series(series_raw)

        # Build a clean title: series + "Chapter N"
        title_parts = []
        if series:
            title_parts.append(series)
        title_parts.append(f"Chapter {_fmt_number(num)}")
        title = " ".join(title_parts)

        content_type = "chapter" if not in_chapters_dir else "chapter"
        return ParsedFilename(
            title=title,
            series=series,
            series_index=num,
            content_type=content_type,
        )

    # 5. Volume detection
    volume_match = _RE_VOLUME.search(work)
    if volume_match:
        num = _normalise_number(volume_match.group(1))
        series_raw = work[: volume_match.start()]
        series = _clean_series(series_raw)

        title_parts = []
        if series:
            title_parts.append(series)
        title_parts.append(f"v{_fmt_number(num)}")
        title = " ".join(title_parts)

        content_type = "chapter" if in_chapters_dir else "volume"
        return ParsedFilename(
            title=title,
            series=series,
            series_index=num,
            content_type=content_type,
        )

    # 6. Bare trailing number → chapter
    bare_match = _RE_BARE_NUMBER.match(work)
    if bare_match:
        series_raw = bare_match.group(1)
        num = _normalise_number(bare_match.group(2))
        series = _clean_series(series_raw)

        title_parts = []
        if series:
            title_parts.append(series)
        title_parts.append(_fmt_number(num))
        title = " ".join(title_parts)

        content_type = "chapter"
        return ParsedFilename(
            title=title,
            series=series,
            series_index=num,
            content_type=content_type,
        )

    # 7. Fallback: no number detected → volume, series=None
    content_type = "chapter" if in_chapters_dir else "volume"
    return ParsedFilename(
        title=work,
        series=None,
        series_index=None,
        content_type=content_type,
    )


def _fmt_number(n: float) -> str:
    """Format a float nicely: 1.0 → '1', 1.5 → '1.5'."""
    return str(int(n)) if n == int(n) else str(n)
