"""Tests for the Reading DNA service (backend/services/reading_dna.py)."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services.reading_dna import compute_reading_dna


def _finish(db: Session, user_id: int, book, *, when: datetime, word_count: int,
            night: bool, duration: int = 1800) -> None:
    """Mark a book read and log a session at a night/morning hour."""
    book.word_count = word_count
    db.add(UserBookStatus(user_id=user_id, book_id=book.id, status="read", updated_at=when))
    hour = 23 if night else 8
    started = when.replace(hour=hour, minute=0, second=0, microsecond=0)
    db.add(ReadingSession(
        user_id=user_id, book_id=book.id,
        started_at=started, ended_at=started + timedelta(seconds=duration),
        duration_seconds=duration, pages_turned=30,
    ))
    db.flush()


def test_dna_empty_when_no_data(db: Session, admin_user):
    user, _ = admin_user
    dna = compute_reading_dna(db, user, 0)
    assert dna["ready"] is False
    assert dna["archetype"] is None
    assert dna["traits"] == []


def test_dna_night_owl_epic_specialist(db: Session, admin_user, make_book):
    user, _ = admin_user
    base = datetime.utcnow() - timedelta(days=10)
    # Four long books, same author, read late at night.
    for i in range(4):
        b = make_book(title=f"Epic {i}", author="One Author")
        _finish(db, user.id, b, when=base + timedelta(days=i), word_count=190_000, night=True)

    dna = compute_reading_dna(db, user, 0)
    assert dna["ready"] is True
    traits = {t["key"]: t for t in dna["traits"]}

    # Long books → length leans high; late nights → time leans high.
    assert traits["length"]["score"] > 50
    assert traits["time"]["score"] > 50
    # One author across all reads → focused (low variety).
    assert traits["variety"]["score"] < 50
    # Pole labels are carried for the UI.
    assert traits["length"]["low"] == "Short reads" and traits["length"]["high"] == "Long epics"

    # Archetype assembled from the two most-extreme traits, never contradictory.
    assert dna["archetype"]
    assert dna["summary"]


def test_dna_short_morning_reader_diverges(db: Session, admin_user, make_book):
    user, _ = admin_user
    base = datetime.utcnow() - timedelta(days=10)
    for i in range(4):
        b = make_book(title=f"Novella {i}", author=f"Author {i}")
        _finish(db, user.id, b, when=base + timedelta(days=i), word_count=40_000, night=False)

    dna = compute_reading_dna(db, user, 0)
    traits = {t["key"]: t for t in dna["traits"]}
    # Short books → length low; mornings → time low; many authors → variety high.
    assert traits["length"]["score"] < 50
    assert traits["time"]["score"] < 50
    assert traits["variety"]["score"] > 50
