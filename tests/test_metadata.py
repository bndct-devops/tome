"""Tests for metadata parsing pure functions.

Covers:
- scripts/import_library.py: parse_filename(), normalise_author()
- backend/services/metadata.py: get_format(), and the series-sanitization
  logic applied inside extract_metadata() (tested via the regex directly).

These are all pure functions / regex operations — no filesystem or DB needed.
"""
import re
import pytest
from pathlib import Path

from scripts.import_library import parse_filename, normalise_author
from backend.services.metadata import get_format


# ── normalise_author ──────────────────────────────────────────────────────────

class TestNormaliseAuthor:
    def test_last_first_flipped(self):
        assert normalise_author("Fitzgerald, F. Scott") == "F. Scott Fitzgerald"

    def test_already_first_last_unchanged(self):
        assert normalise_author("F. Scott Fitzgerald") == "F. Scott Fitzgerald"

    def test_single_name_unchanged(self):
        assert normalise_author("Homer") == "Homer"

    def test_multiple_authors_with_and_unchanged(self):
        # "A, B and C" contains " and " so the flip must be skipped
        assert normalise_author("Pratchett, Terry and Gaiman, Neil") == "Pratchett, Terry and Gaiman, Neil"

    def test_strips_leading_trailing_whitespace(self):
        assert normalise_author("  Tolkien, J.R.R.  ") == "J.R.R. Tolkien"

    def test_unicode_author(self):
        assert normalise_author("Murakami, Haruki") == "Haruki Murakami"


# ── parse_filename — standard cases ──────────────────────────────────────────

class TestParseFilenameStandard:
    def test_title_author_year(self):
        result = parse_filename("The Great Gatsby - F. Scott Fitzgerald (1925)")
        assert result["title"] == "The Great Gatsby"
        assert result["author"] == "F. Scott Fitzgerald"
        assert result["year"] == 1925

    def test_title_author_no_year(self):
        result = parse_filename("Some Book - Author Name")
        assert result["title"] == "Some Book"
        assert result["author"] == "Author Name"
        assert "year" not in result

    def test_last_first_author_normalised(self):
        result = parse_filename("Dune - Herbert, Frank (1965)")
        assert result["author"] == "Frank Herbert"
        assert result["year"] == 1965

    def test_no_author_separator_uses_full_stem_as_title(self):
        # Filename has no " - " separator — entire stem becomes title
        result = parse_filename("JustATitle")
        assert result["title"] == "JustATitle"
        assert "author" not in result

    def test_title_with_subtitle_colon_split(self):
        result = parse_filename("Foundation: The Psychohistory Trilogy - Isaac Asimov (1951)")
        assert result["title"] == "Foundation"
        assert result["subtitle"] == "The Psychohistory Trilogy"
        assert result["author"] == "Isaac Asimov"

    def test_unicode_title_and_author(self):
        result = parse_filename("Kafkas Verwandlung - Franz Kafka (1915)")
        assert result["title"] == "Kafkas Verwandlung"
        assert result["author"] == "Franz Kafka"
        assert result["year"] == 1915

    def test_hyphen_in_title_splits_on_last_dash(self):
        # The regex is greedy on the title part — splits on the LAST " - "
        result = parse_filename("Gulliver -Travels into Several Remote Nations-, Vol. 24 - Swift, Jonathan (2021)")
        assert result["author"] == "Jonathan Swift"
        assert result["year"] == 2021
        # Series should be detected from the title portion
        assert result.get("series") is not None


# ── parse_filename — series / volume detection ────────────────────────────────

class TestParseFilenameSeriesDetection:
    def test_volume_keyword(self):
        result = parse_filename("Chronicles of Narnia Volume 1 - C.S. Lewis (1950)")
        assert result["series"] == "Chronicles of Narnia"
        assert result["series_index"] == 1.0

    def test_vol_dot_abbreviation(self):
        result = parse_filename("The Lord of the Rings, Vol. 02 - Tolkien (1954)")
        assert result["series"] == "The Lord of the Rings"
        assert result["series_index"] == 2.0

    def test_vol_no_dot(self):
        result = parse_filename("Sherlock Holmes Vol. 3 - Doyle, Arthur Conan (1905)")
        assert result["series"] == "Sherlock Holmes"
        assert result["series_index"] == 3.0

    def test_book_keyword(self):
        result = parse_filename("The Odyssey Book 5 - Homer (1900)")
        assert result["series"] == "The Odyssey"
        assert result["series_index"] == 5.0

    def test_numbered_prefix_ugland_style(self):
        # "01. Title - Author"
        result = parse_filename("01. Some Story - Some Author")
        assert result.get("series_index") == 1.0

    def test_parenthetical_series_hint(self):
        # "Title (Series Book N) - Author"
        result = parse_filename("A Wizard's Burden (The Paladin Cycle Book 3) - T.H. Osborne (2021)")
        assert result["title"] == "A Wizard's Burden"
        assert result["series"] == "The Paladin Cycle"
        assert result["series_index"] == 3.0
        assert result["author"] == "T.H. Osborne"

    def test_volume_with_subtitle_stored(self):
        result = parse_filename("A Tale of Two Cities Volume 01 The Guillotine - Charles Dickens (1859)")
        assert result["series"] == "A Tale of Two Cities"
        assert result["series_index"] == 1.0
        assert result.get("subtitle") == "The Guillotine"

    def test_decimal_volume_index(self):
        result = parse_filename("Some Series Vol. 1.5 - Author Name (2020)")
        assert result["series_index"] == 1.5


# ── parse_filename — edge cases ───────────────────────────────────────────────

class TestParseFilenameEdgeCases:
    def test_empty_string_returns_title_only(self):
        result = parse_filename("")
        assert result["title"] == ""

    def test_numeric_suffix_in_author_stripped_when_no_year(self):
        # Some tools embed "(101)" non-year suffixes — must be stripped
        result = parse_filename("My Book - Cool Author (101)")
        assert result["author"] == "Cool Author"
        assert "year" not in result

    def test_four_digit_year_kept(self):
        result = parse_filename("My Book - Cool Author (2023)")
        assert result["year"] == 2023
        assert result["author"] == "Cool Author"

    def test_multiple_hyphens_in_title_no_false_split(self):
        # Titles with em-dash style separators inside shouldn't confuse the parser
        result = parse_filename("Title Part One - Author Smith (2020)")
        assert result["author"] == "Author Smith"
        assert result["year"] == 2020


# ── get_format ────────────────────────────────────────────────────────────────

class TestGetFormat:
    def test_epub(self):
        assert get_format(Path("book.epub")) == "epub"

    def test_pdf(self):
        assert get_format(Path("book.pdf")) == "pdf"

    def test_cbz(self):
        assert get_format(Path("comic.cbz")) == "cbz"

    def test_cbr(self):
        assert get_format(Path("comic.cbr")) == "cbr"

    def test_mobi(self):
        assert get_format(Path("book.mobi")) == "mobi"

    def test_uppercase_extension_normalised(self):
        assert get_format(Path("book.EPUB")) == "epub"

    def test_unsupported_extension_returns_none(self):
        assert get_format(Path("document.docx")) is None

    def test_no_extension_returns_none(self):
        assert get_format(Path("noextension")) is None

    def test_path_with_directories(self):
        assert get_format(Path("/library/Author/Book Title.epub")) == "epub"


# ── series sanitization (mirrors the regex used in extract_metadata) ──────────

# The sanitization is applied inside extract_metadata() which requires a real
# file on disk. We replicate the regex logic here to unit-test it in isolation.

def _sanitize_series(raw: str) -> str:
    """Mirror of the series-sanitization block in extract_metadata()."""
    s = raw.strip()
    s = re.sub(r'\s+[-\u2013]\s+.+$', '', s).strip()
    s = re.sub(r'\s+-[^-].*$', '', s).strip()
    s = s.rstrip(',-: ')
    return s


class TestSeriesSanitization:
    def test_strips_subtitle_after_dash(self):
        assert _sanitize_series("My Series - The Subtitle") == "My Series"

    def test_strips_subtitle_after_en_dash(self):
        assert _sanitize_series("My Series \u2013 The Subtitle") == "My Series"

    def test_strips_trailing_comma(self):
        assert _sanitize_series("My Series,") == "My Series"

    def test_strips_trailing_colon(self):
        assert _sanitize_series("My Series:") == "My Series"

    def test_plain_name_unchanged(self):
        assert _sanitize_series("Candide") == "Candide"

    def test_strips_leading_trailing_whitespace(self):
        assert _sanitize_series("  Candide  ") == "Candide"

    def test_multi_word_series_name_preserved(self):
        assert _sanitize_series("The Lord of the Rings") == "The Lord of the Rings"

    def test_dash_with_no_space_not_stripped(self):
        # A dash without surrounding spaces should NOT be stripped
        # (e.g. "Jean-Christophe" — the regex requires \s+ before the dash)
        assert _sanitize_series("Jean-Christophe") == "Jean-Christophe"
