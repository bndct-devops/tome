"""Hardcover tokens work with or without the "Bearer " prefix."""
from backend.services.hardcover_token import hardcover_auth


def test_bare_token_gets_the_prefix():
    assert hardcover_auth("hc_tok") == "Bearer hc_tok"


def test_prefixed_token_is_kept_once():
    assert hardcover_auth("Bearer hc_tok") == "Bearer hc_tok"


def test_prefix_case_and_whitespace_are_forgiven():
    assert hardcover_auth("  bearer   hc_tok \n") == "Bearer hc_tok"
    assert hardcover_auth("BEARER hc_tok") == "Bearer hc_tok"


def test_empty_stays_empty():
    assert hardcover_auth("") == ""
    assert hardcover_auth("   ") == ""
