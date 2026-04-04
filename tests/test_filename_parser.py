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
