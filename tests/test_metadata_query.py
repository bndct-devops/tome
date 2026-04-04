"""
Tests for pure query-building and string-manipulation functions in
backend/services/metadata_fetch.py  — no network calls required.
"""

from backend.services.metadata_fetch import (
    _build_hardcover_query,
    _build_query,
    _clean_title,
    _clean_series_name,
    _title_is_series_variant,
    _first_author_token,
    MetadataCandidate,
)


# ---------------------------------------------------------------------------
# Post-filter helper (replicates _hardcover() post-filter logic)
# ---------------------------------------------------------------------------

def _apply_series_filter(
    candidates: list[MetadataCandidate],
    series: str | None,
) -> list[MetadataCandidate]:
    """Replicates the post-filter logic from _hardcover()."""
    if not series or not candidates:
        return candidates
    series_lower = series.lower()
    series_words = {w for w in series_lower.split() if len(w) >= 3}
    filtered = []
    for c in candidates:
        c_title_lower = c.title.lower()
        if series_lower in c_title_lower:
            filtered.append(c)
        elif series_words and sum(1 for w in series_words if w in c_title_lower) >= len(series_words) * 0.6:
            filtered.append(c)
    return filtered if filtered else candidates


def _make_candidate(title: str) -> MetadataCandidate:
    return MetadataCandidate(
        source="hardcover",
        source_id="1",
        title=title,
        author="Test Author",
    )


# ---------------------------------------------------------------------------
# TestBuildHardcoverQuery
# ---------------------------------------------------------------------------

class TestBuildHardcoverQuery:
    def test_override_passes_through(self) -> None:
        result = _build_hardcover_query("anything", None, None, None, None, "my override")
        assert result == "my override"

    def test_isbn_takes_priority(self) -> None:
        result = _build_hardcover_query("Some Title", "Author", "978-0-123", "Series", 1.0)
        assert result == "978-0-123"

    def test_series_variant_uses_series_name_and_volume(self) -> None:
        # Title equals series name — use clean series name + integer volume, no "Vol"
        result = _build_hardcover_query("Faust", None, None, "Faust", 1.0)
        assert result == "Faust 1"
        assert "Vol" not in result

    def test_series_variant_decimal_volume(self) -> None:
        result = _build_hardcover_query("Moby Dick", None, None, "Moby Dick", 10.5)
        assert result == "Moby Dick 10.5"

    def test_series_variant_cleans_subtitle(self) -> None:
        # Long series name with subtitle — _clean_series_name strips it
        series = "Gulliver -Travels into Several Remote Nations-"
        result = _build_hardcover_query(series, None, None, series, 1.0)
        assert result == "Gulliver 1"

    def test_vNN_in_title_extracts_volume(self) -> None:
        # "v01" found in title — clean_title strips noise, vol_num extracted
        result = _build_hardcover_query(
            "Faust v01 (2018) (Omnibus Edition) (Digital)", None, None, None, None
        )
        assert result == "Faust 1"

    def test_title_with_author_no_series(self) -> None:
        # No series, no ISBN, no vNN — falls through to clean_title + full author
        result = _build_hardcover_query("The Final Empire", "Bram Stoker", None, None, None)
        assert result == "The Final Empire Bram Stoker"

    def test_plain_title_only(self) -> None:
        result = _build_hardcover_query("Mistborn", None, None, None, None)
        assert result == "Mistborn"

    def test_non_series_title_not_treated_as_series(self) -> None:
        # Title "A Modest Proposal" doesn't start with series "The Explorers"
        # so _title_is_series_variant returns False; falls through to title path
        result = _build_hardcover_query("A Modest Proposal", None, None, "The Explorers", 3.0)
        assert result == "A Modest Proposal"

    def test_isbn_beats_override_when_no_override(self) -> None:
        # Sanity check: with override=None, ISBN is used
        result = _build_hardcover_query("Title", "Author", "9780000000001", "Series", 1.0, None)
        assert result == "9780000000001"

    def test_series_integer_volume_strips_decimal(self) -> None:
        # 5.0 should render as 5, not 5.0
        result = _build_hardcover_query("Berserk", None, None, "Berserk", 5.0)
        assert result == "Berserk 5"
        assert "5.0" not in result


# ---------------------------------------------------------------------------
# TestBuildGoogleOLQuery
# ---------------------------------------------------------------------------

class TestBuildGoogleOLQuery:
    def test_override_passes_through(self) -> None:
        result = _build_query("t", "a", None, None, None, "override")
        assert result == "override"

    def test_isbn_query(self) -> None:
        result = _build_query("Title", None, "978-0-123", None, None, None)
        assert result == "isbn:978-0-123"

    def test_series_variant_uses_intitle(self) -> None:
        # Google/OL query includes "Vol" (unlike Hardcover)
        result = _build_query("Faust", None, None, "Faust", 1.0, None)
        assert result == "intitle:Faust Vol 1"

    def test_series_variant_with_author(self) -> None:
        result = _build_query("Faust", "Johann Goethe", None, "Faust", 1.0, None)
        assert result == "intitle:Faust Vol 1 inauthor:Goethe"

    def test_plain_title_with_author(self) -> None:
        result = _build_query("The Final Empire", "Bram Stoker", None, None, None, None)
        assert result == "intitle:The Final Empire inauthor:Stoker"

    def test_isbn_takes_priority_over_series(self) -> None:
        result = _build_query("Title", "Author", "9780000000001", "Series", 1.0, None)
        assert result == "isbn:9780000000001"

    def test_non_series_title_uses_clean_title(self) -> None:
        # Title not a variant of series → clean title + author
        result = _build_query("A Modest Proposal", None, None, "The Explorers", 3.0, None)
        assert result == "intitle:A Modest Proposal"


# ---------------------------------------------------------------------------
# TestCleanTitle
# ---------------------------------------------------------------------------

class TestCleanTitle:
    def test_strips_parenthesized_groups(self) -> None:
        # Parens stripped; v01 also stripped by vNN rule before parens rule runs,
        # then remaining parens are stripped
        result = _clean_title("Faust v01 (2018) (Digital)")
        assert result == "Faust"

    def test_strips_litrpg_suffix(self) -> None:
        result = _clean_title("Defiance of the Fall A LitRPG Adventure")
        assert result == "Defiance of the Fall"

    def test_strips_subtitle_after_dash(self) -> None:
        # " - " followed by 3+ words is stripped
        result = _clean_title("Title - A Long Subtitle Here")
        assert result == "Title"

    def test_strips_volume_markers(self) -> None:
        result = _clean_title("Solo Leveling, Vol. 1")
        assert result == "Solo Leveling"

    def test_preserves_plain_title(self) -> None:
        result = _clean_title("Mistborn")
        assert result == "Mistborn"

    def test_strips_multiple_parenthesized_groups(self) -> None:
        result = _clean_title("Some Book (2020) (Omnibus) (Digital)")
        assert result == "Some Book"

    def test_strips_gamelit_suffix(self) -> None:
        result = _clean_title("Title A Gamelit Novel")
        assert result == "Title"


# ---------------------------------------------------------------------------
# TestCleanSeriesName
# ---------------------------------------------------------------------------

class TestCleanSeriesName:
    def test_strips_dash_subtitle(self) -> None:
        result = _clean_series_name("Gulliver -Travels into Several Remote Nations-")
        assert result == "Gulliver"

    def test_strips_trailing_punctuation(self) -> None:
        result = _clean_series_name("My Series:")
        assert result == "My Series"

    def test_preserves_plain_name(self) -> None:
        result = _clean_series_name("Moby Dick")
        assert result == "Moby Dick"

    def test_strips_em_dash_subtitle(self) -> None:
        # En/em dash variant
        result = _clean_series_name("Series Name \u2013 Long Subtitle Text")
        assert result == "Series Name"

    def test_strips_spaced_dash_subtitle(self) -> None:
        result = _clean_series_name("The Lord of the Rings - The Return of the King")
        assert result == "The Lord of the Rings"


# ---------------------------------------------------------------------------
# TestTitleIsSeriesVariant
# ---------------------------------------------------------------------------

class TestTitleIsSeriesVariant:
    def test_exact_match(self) -> None:
        assert _title_is_series_variant("Faust", "Faust") is True

    def test_title_starts_with_series(self) -> None:
        assert _title_is_series_variant("Gulliver Vol 1", "Gulliver") is True

    def test_completely_different(self) -> None:
        assert _title_is_series_variant("Pride and Prejudice", "Narnia") is False

    def test_empty_series(self) -> None:
        assert _title_is_series_variant("Anything", "") is False

    def test_case_insensitive(self) -> None:
        assert _title_is_series_variant("faust vol 1", "Faust") is True

    def test_title_with_long_series_prefix(self) -> None:
        # Series name is long — prefix is up to 20 chars
        series = "The Chronicles of Narnia"
        title = "The Chronicles of Narnia, Vol. 1"
        assert _title_is_series_variant(title, series) is True

    def test_partial_series_not_a_variant(self) -> None:
        # Title only shares a short prefix accidentally — should still match
        # based on prefix rule (up to 20 chars)
        assert _title_is_series_variant("The Chr", "The Chronicles of Narnia") is False


# ---------------------------------------------------------------------------
# TestFirstAuthorToken
# ---------------------------------------------------------------------------

class TestFirstAuthorToken:
    def test_single_name(self) -> None:
        assert _first_author_token("Homer") == "Homer"

    def test_full_name_returns_surname(self) -> None:
        assert _first_author_token("Bram Stoker") == "Stoker"

    def test_multiple_authors_comma_separated(self) -> None:
        # Only first author's surname returned
        assert _first_author_token("Bram Stoker, Mary Shelley") == "Stoker"

    def test_authors_joined_with_and(self) -> None:
        assert _first_author_token("Jane Austen and Seth Grahame-Smith") == "Austen"

    def test_none_returns_none(self) -> None:
        assert _first_author_token(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _first_author_token("") is None


# ---------------------------------------------------------------------------
# TestHardcoverPostFilter
# ---------------------------------------------------------------------------

class TestHardcoverPostFilter:
    def test_keeps_matching_candidates(self) -> None:
        candidates = [_make_candidate("Faust Vol 1"), _make_candidate("Faust Omnibus 1")]
        result = _apply_series_filter(candidates, "Faust")
        assert len(result) == 2
        titles = {c.title for c in result}
        assert "Faust Vol 1" in titles
        assert "Faust Omnibus 1" in titles

    def test_removes_unrelated_candidates(self) -> None:
        candidates = [_make_candidate("Faust Vol 1"), _make_candidate("Canterbury Tales")]
        result = _apply_series_filter(candidates, "Faust")
        assert len(result) == 1
        assert result[0].title == "Faust Vol 1"

    def test_falls_back_to_all_if_none_match(self) -> None:
        # No candidate matches "Faust" — fallback keeps all originals
        candidates = [_make_candidate("Canterbury Tales"), _make_candidate("Jean-Christophe")]
        result = _apply_series_filter(candidates, "Faust")
        assert len(result) == 2

    def test_partial_word_match_at_60_percent(self) -> None:
        # "Solo Leveling": significant words = {"solo", "leveling"} (both 4+ chars)
        # "Solo Leveling Vol 1" has both → 2/2 = 100% >= 60% → kept
        # "Solo Farming" has "solo" only → 1/2 = 50% < 60% → not kept
        candidates = [
            _make_candidate("Solo Leveling Vol 1"),
            _make_candidate("Solo Farming"),
        ]
        result = _apply_series_filter(candidates, "Solo Leveling")
        assert len(result) == 1
        assert result[0].title == "Solo Leveling Vol 1"

    def test_no_filter_when_series_is_none(self) -> None:
        candidates = [_make_candidate("Random Book"), _make_candidate("Another Book")]
        result = _apply_series_filter(candidates, None)
        assert len(result) == 2

    def test_no_filter_when_candidates_empty(self) -> None:
        result = _apply_series_filter([], "Faust")
        assert result == []

    def test_exact_series_name_in_title(self) -> None:
        candidates = [_make_candidate("Moby Dick Vol 100")]
        result = _apply_series_filter(candidates, "Moby Dick")
        assert len(result) == 1

    def test_short_words_excluded_from_series_words(self) -> None:
        # "My Hero Academia" — "my" (2 chars) excluded, "hero" + "academia" remain
        # A title containing "hero academia" should match
        candidates = [
            _make_candidate("My Hero Academia Vol 1"),
            _make_candidate("The Hero"),  # only "hero" matches → 1/2 = 50% < 60%
        ]
        result = _apply_series_filter(candidates, "My Hero Academia")
        assert len(result) == 1
        assert result[0].title == "My Hero Academia Vol 1"
