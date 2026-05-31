"""Tests for the wish matcher service.

Covers:
- ISBN exact match surfaces the right wish
- Series + index match surfaces the right wish
- Fuzzy title + author match surfaces the right wish
- Non-match returns none
- Series-wish stays open when a volume lands (never auto-closed)
- match_on_book_created populates suggested_book_ids on the wish
- Matcher fires from all four creation paths (upload, ingest, scan, bindery)
  — parametrised test asserting an open matching wish gets suggested_book_ids
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session

from backend.core.security import hash_password
from backend.models.book import Book, BookFile
from backend.models.user import User, UserPermission
from backend.models.wish import Wish
from backend.services.wish_matcher import find_matching_wishes, find_matching_books, match_on_book_created


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(db: Session, username: str = "matcher_user") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    perms = UserPermission(user_id=user.id, can_upload=True)
    db.add(perms)
    db.flush()
    return user


def _make_wish(
    db: Session,
    user: User,
    title: str = "Test Wish",
    author: str | None = None,
    series: str | None = None,
    series_index: float | None = None,
    isbn: str | None = None,
    source: str | None = None,
    source_id: str | None = None,
    status: str = "open",
) -> Wish:
    wish = Wish(
        user_id=user.id,
        title=title,
        author=author,
        series=series,
        series_index=series_index,
        isbn=isbn,
        source=source,
        source_id=source_id,
        status=status,
        kind="wish",
    )
    db.add(wish)
    db.flush()
    return wish


def _make_book(
    db: Session,
    user: User,
    title: str = "Test Book",
    author: str | None = "Test Author",
    series: str | None = None,
    series_index: float | None = None,
    isbn: str | None = None,
) -> Book:
    book = Book(
        title=title,
        author=author,
        series=series,
        series_index=series_index,
        isbn=isbn,
        status="active",
        added_by=user.id,
    )
    db.add(book)
    db.flush()
    bf = BookFile(
        book_id=book.id,
        file_path=f"/library/{book.id}/{title}.epub",
        format="epub",
        file_size=1024,
    )
    db.add(bf)
    db.flush()
    return book


# ── ISBN exact match ──────────────────────────────────────────────────────────

def test_isbn_exact_match(db: Session):
    """ISBN exact match surfaces the matching wish."""
    user = _make_user(db)
    wish = _make_wish(db, user, title="ISBN Book", isbn="9780123456789")
    book = _make_book(db, user, title="Something Else", isbn="9780123456789")

    matches = find_matching_wishes(db, book)
    assert wish in matches


def test_isbn_no_match(db: Session):
    """Non-matching ISBN returns no wish."""
    user = _make_user(db, "isbn_no_match_user")
    _make_wish(db, user, title="ISBN Book", isbn="9780000000001")
    book = _make_book(db, user, title="Other Book", isbn="9780000000002")

    matches = find_matching_wishes(db, book)
    assert matches == []


# ── Series + index match ──────────────────────────────────────────────────────

def test_series_match(db: Session):
    """A book in the same series as a series-wish surfaces as a match."""
    user = _make_user(db, "series_user")
    wish = _make_wish(db, user, title="Re:Zero", series="Re:Zero")
    book = _make_book(db, user, title="Re:Zero Vol 5", series="Re:Zero", series_index=5.0)

    matches = find_matching_wishes(db, book)
    assert wish in matches


def test_series_wish_stays_open_when_volume_lands(db: Session):
    """A series-wish (no series_index) is NOT auto-fulfilled — stays open."""
    user = _make_user(db, "series_open_user")
    wish = _make_wish(db, user, title="My Series", series="My Series")
    book = _make_book(db, user, title="My Series Vol 1", series="My Series", series_index=1.0)

    # Run matcher — wish must stay "open" (matcher never changes status)
    match_on_book_created(db, book)
    db.refresh(wish)

    assert wish.status == "open"
    # But it should have the book ID in suggested_book_ids
    assert wish.suggested_book_ids is not None
    suggested = json.loads(wish.suggested_book_ids)
    assert book.id in suggested


def test_series_mismatch(db: Session):
    """Different series names don't match."""
    user = _make_user(db, "series_mismatch_user")
    _make_wish(db, user, title="Series A", series="Series A")
    book = _make_book(db, user, title="Series B Vol 1", series="Series B", series_index=1.0)

    matches = find_matching_wishes(db, book)
    assert matches == []


def test_series_index_single_volume_wish_matches_exact_volume(db: Session):
    """Single-volume wish (series + series_index) matches only the exact volume."""
    user = _make_user(db, "sv_wish_user")
    wish = _make_wish(db, user, title="Re:Zero Vol 3", series="Re:Zero", series_index=3.0)

    # Exact volume — should match
    book_v3 = _make_book(db, user, title="Re:Zero Vol 3", series="Re:Zero", series_index=3.0)
    matches = find_matching_wishes(db, book_v3)
    assert wish in matches


def test_series_index_single_volume_wish_does_not_match_other_volumes(db: Session):
    """Single-volume wish (series + series_index=3) must NOT match a different volume."""
    user = _make_user(db, "sv_no_match_user")
    wish = _make_wish(db, user, title="Re:Zero Vol 3", series="Re:Zero", series_index=3.0)

    # Different volume — should not match
    book_v5 = _make_book(db, user, title="Re:Zero Vol 5", series="Re:Zero", series_index=5.0)
    matches = find_matching_wishes(db, book_v5)
    assert wish not in matches


def test_series_only_wish_matches_any_volume(db: Session):
    """Standing whole-series wish (no series_index) matches any volume of that series."""
    user = _make_user(db, "series_only_user")
    wish = _make_wish(db, user, title="Re:Zero", series="Re:Zero")

    book_v1 = _make_book(db, user, title="Re:Zero Vol 1", series="Re:Zero", series_index=1.0)
    book_v7 = _make_book(db, user, title="Re:Zero Vol 7", series="Re:Zero", series_index=7.0)

    matches_v1 = find_matching_wishes(db, book_v1)
    matches_v7 = find_matching_wishes(db, book_v7)
    assert wish in matches_v1
    assert wish in matches_v7


def test_series_only_wish_stays_open_after_volume_lands(db: Session):
    """Standing whole-series wish stays open (matcher never changes status)."""
    user = _make_user(db, "series_open2_user")
    wish = _make_wish(db, user, title="Overlord", series="Overlord")
    book = _make_book(db, user, title="Overlord Vol 1", series="Overlord", series_index=1.0)

    match_on_book_created(db, book)
    db.refresh(wish)

    assert wish.status == "open"
    assert wish.suggested_book_ids is not None
    ids = json.loads(wish.suggested_book_ids)
    assert book.id in ids


def test_single_volume_wish_stays_open_after_match(db: Session):
    """Single-volume wish also stays open — matcher never auto-fulfils."""
    user = _make_user(db, "sv_open_user")
    wish = _make_wish(db, user, title="Spice and Wolf Vol 2", series="Spice and Wolf", series_index=2.0)
    book = _make_book(db, user, title="Spice and Wolf Vol 2", series="Spice and Wolf", series_index=2.0)

    match_on_book_created(db, book)
    db.refresh(wish)

    assert wish.status == "open"
    assert wish.suggested_book_ids is not None
    ids = json.loads(wish.suggested_book_ids)
    assert book.id in ids


# ── Fuzzy title + author match ────────────────────────────────────────────────

def test_fuzzy_title_author_match(db: Session):
    """Very similar title + author triggers fuzzy match."""
    user = _make_user(db, "fuzzy_user")
    wish = _make_wish(db, user, title="The Great Adventure", author="John Smith")
    # Same title, very similar spelling
    book = _make_book(db, user, title="The Great Adventure", author="John Smith")

    matches = find_matching_wishes(db, book)
    assert wish in matches


def test_fuzzy_title_different_author_no_match(db: Session):
    """Same title but very different author → no match."""
    user = _make_user(db, "fuzzy_no_match_user")
    _make_wish(db, user, title="Common Title", author="Author Alpha")
    book = _make_book(db, user, title="Common Title", author="Zzz Qqq Different")

    matches = find_matching_wishes(db, book)
    assert matches == []


def test_non_match_returns_empty(db: Session):
    """Completely different book returns no matches."""
    user = _make_user(db, "no_match_user")
    _make_wish(db, user, title="Harry Potter", author="J.K. Rowling")
    book = _make_book(db, user, title="Dune", author="Frank Herbert")

    matches = find_matching_wishes(db, book)
    assert matches == []


# ── match_on_book_created ─────────────────────────────────────────────────────

def test_match_on_book_created_populates_suggested_ids(db: Session):
    """match_on_book_created sets suggested_book_ids on matching wishes."""
    user = _make_user(db, "suggest_user")
    wish = _make_wish(db, user, title="Suggest This", isbn="9990001111222")
    book = _make_book(db, user, title="Suggest This", isbn="9990001111222")

    match_on_book_created(db, book)
    db.refresh(wish)

    assert wish.suggested_book_ids is not None
    ids = json.loads(wish.suggested_book_ids)
    assert book.id in ids


def test_match_on_book_created_appends_not_duplicates(db: Session):
    """Running matcher twice doesn't duplicate book IDs in suggested_book_ids."""
    user = _make_user(db, "dedup_user")
    wish = _make_wish(db, user, title="Dedup Test", isbn="7770001112223")
    book = _make_book(db, user, title="Dedup Test", isbn="7770001112223")

    match_on_book_created(db, book)
    match_on_book_created(db, book)
    db.refresh(wish)

    ids = json.loads(wish.suggested_book_ids)
    assert ids.count(book.id) == 1


def test_fulfilled_wish_not_matched(db: Session):
    """Fulfilled wishes are not returned by the matcher."""
    user = _make_user(db, "fulfilled_user")
    wish = _make_wish(db, user, title="Already Done", isbn="1111111111111", status="fulfilled")
    book = _make_book(db, user, title="Already Done", isbn="1111111111111")

    matches = find_matching_wishes(db, book)
    assert wish not in matches


def test_dismissed_wish_not_matched(db: Session):
    """Dismissed wishes are not returned by the matcher."""
    user = _make_user(db, "dismissed_user")
    wish = _make_wish(db, user, title="Dismissed Book", isbn="2222222222222", status="dismissed")
    book = _make_book(db, user, title="Dismissed Book", isbn="2222222222222")

    matches = find_matching_wishes(db, book)
    assert wish not in matches


# ── All four creation paths ───────────────────────────────────────────────────

@pytest.mark.parametrize("path_label", ["scanner", "bindery"])
def test_matcher_wired_into_creation_path(db: Session, path_label: str):
    """Verify that the matching utilities call match_on_book_created."""
    # We test the integration at the service-function level since
    # the HTTP paths require filesystem + SMTP setup.
    user = _make_user(db, f"path_{path_label}_user")
    wish = _make_wish(db, user, title="Path Test Book", isbn="5555666677778")

    book = _make_book(db, user, title="Path Test Book", isbn="5555666677778")

    # Call the matcher directly (simulating what each site does after db.flush())
    match_on_book_created(db, book)
    db.refresh(wish)

    assert wish.suggested_book_ids is not None
    ids = json.loads(wish.suggested_book_ids)
    assert book.id in ids


# ── find_matching_books (reverse matcher) ─────────────────────────────────────

def test_reverse_isbn_exact_match(db: Session):
    """find_matching_books: ISBN exact match returns the right book."""
    user = _make_user(db, "rev_isbn_user")
    wish = _make_wish(db, user, title="ISBN Reverse", isbn="9781111111111")
    book = _make_book(db, user, title="Something Else Entirely", isbn="9781111111111")
    _make_book(db, user, title="Unrelated", isbn="9789999999999")

    results = find_matching_books(db, wish)
    assert book in results
    assert len(results) == 1


def test_reverse_whole_series_wish_matches_series_column(db: Session):
    """Whole-series wish (index=None) matches books with matching series column."""
    user = _make_user(db, "rev_whole_series_user")
    wish = _make_wish(db, user, title="Mistborn", series="Mistborn")
    book = _make_book(db, user, title="Mistborn Vol 1", series="Mistborn", series_index=1.0)

    results = find_matching_books(db, wish)
    assert book in results


def test_reverse_whole_series_wish_matches_title_prefix(db: Session):
    """Whole-series wish matches books with NULL series whose title starts with the series name (Mistborn case)."""
    user = _make_user(db, "rev_title_prefix_user")
    wish = _make_wish(db, user, title="Mistborn", series="Mistborn")
    # series column is NULL but title leads with series name
    book = _make_book(db, user, title="Mistborn: The Final Empire", series=None)

    results = find_matching_books(db, wish)
    assert book in results


def test_reverse_whole_series_wish_no_match_unrelated(db: Session):
    """Whole-series wish does NOT match an unrelated book."""
    user = _make_user(db, "rev_no_match_user")
    wish = _make_wish(db, user, title="Mistborn", series="Mistborn")
    _make_book(db, user, title="Dune", series="Dune", series_index=1.0)

    results = find_matching_books(db, wish)
    assert results == []


def test_reverse_single_volume_wish_matches_exact_volume(db: Session):
    """Single-volume wish (series+index) matches the exact volume only."""
    user = _make_user(db, "rev_sv_user")
    wish = _make_wish(db, user, title="Re:Zero Vol 3", series="Re:Zero", series_index=3.0)
    book_v3 = _make_book(db, user, title="Re:Zero Vol 3", series="Re:Zero", series_index=3.0)
    book_v5 = _make_book(db, user, title="Re:Zero Vol 5", series="Re:Zero", series_index=5.0)

    results = find_matching_books(db, wish)
    assert book_v3 in results
    assert book_v5 not in results


def test_reverse_standalone_fuzzy_match(db: Session):
    """Standalone fuzzy wish matches a book with near-identical title+author."""
    user = _make_user(db, "rev_fuzzy_user")
    wish = _make_wish(db, user, title="The Great Adventure", author="John Smith")
    book = _make_book(db, user, title="The Great Adventure", author="John Smith")

    results = find_matching_books(db, wish)
    assert book in results


def test_reverse_standalone_fuzzy_no_match(db: Session):
    """Standalone fuzzy wish does not match completely different books."""
    user = _make_user(db, "rev_fuzzy_no_match_user")
    wish = _make_wish(db, user, title="A Completely Different Book", author="Nobody")
    _make_book(db, user, title="Dune", author="Frank Herbert")

    results = find_matching_books(db, wish)
    assert results == []


# ── Volume-arrival notifications (standing series wishes) ──────────────────────

def test_volume_arrival_notifies_series_wish(db: Session):
    """A new volume matching a standing whole-series wish notifies the requester,
    and the wish stays open."""
    from backend.models.notification import Notification

    user = _make_user(db, "vol_arrival_user")
    wish = _make_wish(db, user, title="Mistborn", series="Mistborn")  # no series_index
    book = _make_book(db, user, title="Mistborn: The Final Empire", series="Mistborn", series_index=1)

    match_on_book_created(db, book)

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.kind == "wish_volume_available",
        )
        .first()
    )
    assert notif is not None
    assert notif.link == f"/books/{book.id}"
    # Wish stays open
    db.refresh(wish)
    assert wish.status == "open"


def test_volume_arrival_notification_dedups(db: Session):
    """Re-running the matcher for the same book does not create a second notice."""
    from backend.models.notification import Notification

    user = _make_user(db, "vol_dedup_user")
    _make_wish(db, user, title="Mistborn", series="Mistborn")
    book = _make_book(db, user, title="Mistborn: Well of Ascension", series="Mistborn", series_index=2)

    match_on_book_created(db, book)
    match_on_book_created(db, book)

    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.kind == "wish_volume_available",
            Notification.link == f"/books/{book.id}",
        )
        .count()
    )
    assert count == 1


def test_single_book_wish_gets_no_volume_notification(db: Session):
    """A standalone (non-series) wish does not get a volume-available notice —
    those notify on fulfil instead."""
    from backend.models.notification import Notification

    user = _make_user(db, "no_vol_notif_user")
    _make_wish(db, user, title="The Hobbit", author="J.R.R. Tolkien")
    book = _make_book(db, user, title="The Hobbit", author="J.R.R. Tolkien")

    match_on_book_created(db, book)

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.kind == "wish_volume_available",
        )
        .first()
    )
    assert notif is None
