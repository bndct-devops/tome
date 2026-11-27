"""
Tests for backend/services/filename_parser.py
"""

import pytest
from backend.services.filename_parser import parse_filename, ParsedFilename


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(filename: str, in_chapters_dir: bool = False) -> ParsedFilename:
    return parse_filename(filename, in_chapters_dir=in_chapters_dir)


# ---------------------------------------------------------------------------
# Standard chapter convention — bare numbers
# ---------------------------------------------------------------------------

class TestBareNumberChapters:
    def test_moby_dick_bare_number(self) -> None:
        r = _parse("Moby Dick 1179.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Moby Dick"
        assert r.series_index == 1179.0

    def test_beowulf_bare_number(self) -> None:
        r = _parse("Beowulf 230.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Beowulf"
        assert r.series_index == 230.0

    def test_multi_word_series_bare_number(self) -> None:
        r = _parse("Don Quixote 99.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Don Quixote"
        assert r.series_index == 99.0


# ---------------------------------------------------------------------------
# Standard chapter convention — explicit "Chapter" keyword
# ---------------------------------------------------------------------------

class TestChapterKeyword:
    def test_chapter_keyword_with_redundant_volume(self) -> None:
        r = _parse("Moby Dick Chapter 1179 v1179.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Moby Dick"
        assert r.series_index == 1179.0

    def test_volume_with_the_chapters_it_holds_is_a_volume(self) -> None:
        # Issue #338: a chapter range after the volume number is not a chapter.
        for name in ("Moby Dick Vol 1 Ch 1-7.cbz", "Moby Dick Vol.1 Ch.1 - 7.cbz",
                     "Moby Dick v01 Chapter 1~7 (Digital).cbz"):
            r = _parse(name)
            assert r.content_type == "volume", name
            assert r.series == "Moby Dick", name
            assert r.series_index == 1.0, name

    def test_single_chapter_with_its_volume_stays_a_chapter(self) -> None:
        r = _parse("Moby Dick Vol 1 Ch 3.cbz")
        assert r.content_type == "chapter"
        assert r.series_index == 3.0

    def test_chapter_range_without_a_volume_stays_a_chapter(self) -> None:
        assert _parse("Moby Dick Ch 1-7.cbz").content_type == "chapter"

    def test_chainsaw_man_chapter_with_noise(self) -> None:
        r = _parse("Paradise Lost Chapter 230 v230 (Digital).cbz")
        assert r.content_type == "chapter"
        assert r.series == "Paradise Lost"
        assert r.series_index == 230.0

    def test_ch_abbreviation(self) -> None:
        r = _parse("Iliad Ch.363.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Iliad"
        assert r.series_index == 363.0

    def test_ch_space(self) -> None:
        r = _parse("Iliad Ch 363.cbz")
        assert r.content_type == "chapter"
        assert r.series == "Iliad"
        assert r.series_index == 363.0


# ---------------------------------------------------------------------------
# Standard volume convention
# ---------------------------------------------------------------------------

class TestVolumeConvention:
    def test_beowulf_volume(self) -> None:
        r = _parse("Beowulf v18.cbz")
        assert r.content_type == "volume"
        assert r.series == "Beowulf"
        assert r.series_index == 18.0

    def test_moby_dick_volume(self) -> None:
        r = _parse("Moby Dick v108.cbz")
        assert r.content_type == "volume"
        assert r.series == "Moby Dick"
        assert r.series_index == 108.0

    def test_frieren_with_group_and_noise(self) -> None:
        r = _parse("[1r0n] Gilgamesh Vol.01 (Digital) [CBZ].cbz")
        assert r.content_type == "volume"
        assert r.series == "Gilgamesh"
        assert r.series_index == 1.0

    def test_solo_leveling_with_year_and_noise(self) -> None:
        r = _parse("Don Quixote v01 (2024) (Digital).cbz")
        assert r.content_type == "volume"
        assert r.series == "Don Quixote"
        assert r.series_index == 1.0

    def test_vol_dot_format(self) -> None:
        r = _parse("Dracula Vol.01.epub")
        assert r.content_type == "volume"
        assert r.series == "Dracula"
        assert r.series_index == 1.0

    def test_vol_space_format(self) -> None:
        r = _parse("Dracula Vol 1 - The Castle.epub")
        assert r.content_type == "volume"
        assert r.series == "Dracula"
        assert r.series_index == 1.0

    def test_volume_word_format(self) -> None:
        r = _parse("War and Peace Volume 1.cbz")
        assert r.content_type == "volume"
        assert r.series == "War and Peace"
        assert r.series_index == 1.0


# ---------------------------------------------------------------------------
# EPUB / standard books (no series number)
# ---------------------------------------------------------------------------

class TestEpubFallback:
    def test_standard_epub_no_series(self) -> None:
        r = _parse("The Castle - Bram Stoker.epub")
        assert r.content_type == "volume"
        assert r.series is None
        assert r.series_index is None

    def test_standard_epub_author_dash_title(self) -> None:
        r = _parse("Bram Stoker - The Trial.epub")
        assert r.content_type == "volume"
        assert r.series is None
        assert r.series_index is None


# ---------------------------------------------------------------------------
# Folder override: in_chapters_dir=True forces content_type="chapter"
# ---------------------------------------------------------------------------

class TestFolderOverride:
    def test_volume_file_in_chapters_dir(self) -> None:
        r = _parse("Moby Dick v108.cbz", in_chapters_dir=True)
        assert r.content_type == "chapter"
        assert r.series == "Moby Dick"
        assert r.series_index == 108.0

    def test_fallback_file_in_chapters_dir(self) -> None:
        r = _parse("Some Book.epub", in_chapters_dir=True)
        assert r.content_type == "chapter"
        assert r.series is None
        assert r.series_index is None

    def test_bare_number_in_chapters_dir_stays_chapter(self) -> None:
        # Already detected as chapter; flag is consistent
        r = _parse("Aeneid 700.cbz", in_chapters_dir=True)
        assert r.content_type == "chapter"
        assert r.series == "Aeneid"
        assert r.series_index == 700.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_decimal_chapter(self) -> None:
        r = _parse("Moby Dick 1179.5.cbz")
        assert r.content_type == "chapter"
        assert r.series_index == 1179.5

    def test_no_extension(self) -> None:
        r = _parse("Beowulf v18")
        assert r.content_type == "volume"
        assert r.series == "Beowulf"
        assert r.series_index == 18.0

    def test_multiple_noise_tokens(self) -> None:
        r = _parse("[Scan] Don Quixote v01 (2024) (Digital) [CBZ].cbz")
        assert r.content_type == "volume"
        assert r.series == "Don Quixote"
        assert r.series_index == 1.0

    def test_chapter_takes_priority_over_volume_in_filename(self) -> None:
        # Both "Chapter" keyword and "v230" present — chapter wins
        r = _parse("Paradise Lost Chapter 230 v230 (Digital).cbz")
        assert r.content_type == "chapter"
        assert r.series_index == 230.0

    def test_series_index_is_float(self) -> None:
        r = _parse("Moby Dick 1179.cbz")
        assert isinstance(r.series_index, float)

    def test_series_is_none_for_fallback(self) -> None:
        r = _parse("SomeRandomBook.epub")
        assert r.series is None
        assert r.series_index is None
        assert r.content_type == "volume"


# ---------------------------------------------------------------------------
# Series parenthetical — "(Series Book N)" Amazon/Calibre convention
# ---------------------------------------------------------------------------

class TestSeriesParenthetical:
    def test_series_book_n(self) -> None:
        # The exact prod filename shape behind the original Bindery series bug
        r = _parse("Trick Of The Night A LitRPGGameLit Adventure (The Bad Guys Book 8) - Eric Ugland (2022).epub")
        assert r.series == "The Bad Guys"
        assert r.series_index == 8.0
        assert r.title == "Trick Of The Night A LitRPGGameLit Adventure"
        assert r.author == "Eric Ugland"
        assert r.content_type == "volume"

    def test_series_comma_book_n(self) -> None:
        r = _parse("Unsouled (Cradle, Book 1).epub")
        assert r.series == "Cradle"
        assert r.series_index == 1.0

    def test_series_hash_n(self) -> None:
        r = _parse("The Wandering Inn (The Wandering Inn #7).epub")
        assert r.series == "The Wandering Inn"
        assert r.series_index == 7.0

    def test_plain_year_paren_is_not_series(self) -> None:
        r = _parse("Don Quixote v01 (2024) (Digital).cbz")
        assert r.series == "Don Quixote"
        assert r.series_index == 1.0


# ---------------------------------------------------------------------------
# Structured "NN. Title - Author (Year)" layout (organizer/import convention)
# ---------------------------------------------------------------------------

class TestStructuredLayout:
    def test_index_title_author_year(self) -> None:
        r = _parse("07. Back to One - Eric Ugland (2021).epub")
        assert r.title == "Back to One"
        assert r.author == "Eric Ugland"
        assert r.series_index == 7.0
        assert r.series is None
        assert r.content_type == "volume"

    def test_title_author_year_no_index(self) -> None:
        r = _parse("Dukes and Ladders - Eric Ugland (2021).epub")
        assert r.title == "Dukes and Ladders"
        assert r.author == "Eric Ugland"
        assert r.series is None
        assert r.series_index is None

    def test_index_title_author_no_year(self) -> None:
        r = _parse("01. Scamps & Scoundrels - Eric Ugland.epub")
        assert r.title == "Scamps & Scoundrels"
        assert r.author == "Eric Ugland"
        assert r.series_index == 1.0

    def test_structured_with_inner_volume_marker(self) -> None:
        r = _parse("05. Omniscient Reader v05 - Sing Shong (2021).epub")
        assert r.series == "Omniscient Reader"
        assert r.series_index == 5.0
        assert r.author == "Sing Shong"

    def test_dash_title_without_year_stays_ambiguous(self) -> None:
        # No (Year) anchor and no leading index — can't tell author from title
        r = _parse("The Castle - Bram Stoker.epub")
        assert r.author is None
        assert r.series is None


# ---------------------------------------------------------------------------
# Bare trailing number on prose ebooks must NOT fabricate a series
# (the original Bindery bug: series became the book's own title)
# ---------------------------------------------------------------------------

class TestBareNumberEbook:
    def test_epub_bare_number_no_series(self) -> None:
        r = _parse("Dukes and Ladders 5.epub")
        assert r.series is None
        assert r.series_index == 5.0
        assert r.title == "Dukes and Ladders 5"
        assert r.content_type == "volume"

    def test_pdf_bare_number_no_series(self) -> None:
        r = _parse("Design Patterns 2.pdf")
        assert r.series is None
        assert r.content_type == "volume"

    def test_cbz_bare_number_still_chapter(self) -> None:
        # Comics keep the manga-chapter convention
        r = _parse("One Piece 1050.cbz")
        assert r.series == "One Piece"
        assert r.series_index == 1050.0
        assert r.content_type == "chapter"

    def test_epub_bare_number_in_chapters_dir(self) -> None:
        r = _parse("Some Webnovel 42.epub", in_chapters_dir=True)
        assert r.content_type == "chapter"
        assert r.series_index == 42.0
