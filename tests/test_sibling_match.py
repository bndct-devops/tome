"""Sibling identity matching + candidate-apply tiers (backend/services/sibling_match.py).

The matcher inherits canonical series identity from existing volumes; reviewed
books outvote unreviewed ones so one bad auto-import can't poison the series
once any volume has been human-confirmed.
"""
from backend.services.sibling_match import (
    apply_tier,
    find_series_identity,
)


def test_exact_series_match_inherits_identity(db, make_book):
    v1 = make_book(title="Saga V1", series="Frieren: Beyond Journey's End",
                   series_index=1, author="Kanehito Yamada", language="en")
    v1.is_reviewed = True
    db.commit()
    ident = find_series_identity(db, "Frieren: Beyond Journey's End")
    assert ident is not None
    assert ident.series == "Frieren: Beyond Journey's End"
    assert ident.author == "Kanehito Yamada"
    assert ident.language == "en"
    assert ident.volume_count == 1
    assert ident.from_reviewed


def test_fuzzy_variant_matches_canonical_spelling(db, make_book):
    make_book(title="V1", series="Frieren: Beyond Journey's End",
              series_index=1, author="Kanehito Yamada")
    # The classic Bindery drift case: hyphen instead of colon
    ident = find_series_identity(db, "Frieren - Beyond Journey's End")
    assert ident is not None
    assert ident.series == "Frieren: Beyond Journey's End"


def test_unrelated_series_does_not_match(db, make_book):
    make_book(title="V1", series="Berserk", series_index=1)
    assert find_series_identity(db, "Vinland Saga") is None
    assert find_series_identity(db, None) is None
    assert find_series_identity(db, "   ") is None


def test_reviewed_books_outvote_unreviewed(db, make_book):
    good = make_book(title="V1", series="Overlord", series_index=1,
                     author="Kugane Maruyama")
    bad = make_book(title="V2", series="Overlord", series_index=2,
                    author="Kugane Maruyama (Author), So-bin (Artist)")
    bad2 = make_book(title="V3", series="Overlord", series_index=3,
                     author="Kugane Maruyama (Author), So-bin (Artist)")
    # Majority author is the noisy variant — but only V1 is reviewed
    good.is_reviewed = True
    bad.is_reviewed = False
    bad2.is_reviewed = False
    db.commit()
    ident = find_series_identity(db, "Overlord")
    assert ident.author == "Kugane Maruyama"
    assert ident.from_reviewed
    assert ident.volume_count == 3  # count spans all siblings


def test_author_conflict_refuses_match(db, make_book):
    make_book(title="V1", series="Genesis", series_index=1, author="Alice Author")
    # Same series name, clearly different author → different series
    assert find_series_identity(db, "Genesis", author="Bob Builder") is None
    # Compatible author variant still matches
    assert find_series_identity(db, "Genesis", author="Alice Author") is not None


def test_apply_tiers():
    assert apply_tier(9) == "full"
    assert apply_tier(6) == "full"
    assert apply_tier(5) == "fill"
    assert apply_tier(3) == "fill"
    assert apply_tier(2) == "discard"
    assert apply_tier(0) == "discard"
