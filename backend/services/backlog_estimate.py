"""Backlog completion estimates (issue #187).

How long would a book — or a whole pile of them — take to read at *this
user's* pace? Unlike ``stats/completion-estimates`` (progress-so-far on books
being read), this works for books that haven't been started.

Per book, in order of preference:

1. ``words``    — ``word_count / measured wpm``. The wpm rule mirrors the web
                  reader's pacing endpoint: finished, word-counted books with
                  at least 5 minutes of reconciled read-time.
2. ``default``  — ``word_count / 250`` when the user has no wpm history yet.
3. ``type_avg`` — mean reconciled read-time per *finished* book of the same
                  book type. This is what CBZ/PDF (no word count) fall back to.
4. ``None``     — nothing to go on (no word count, no finished books of that
                  type). The UI says "not estimated" rather than inventing one.

"Days" = total minutes / the user's average minutes per calendar day over the
last 30 days (90 when the last month is empty, so a short break doesn't blank
the forecast). ``None`` when neither window has any reading — a rate of zero
isn't a forecast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.library import BookType
from backend.models.user_book_status import UserBookStatus
from backend.services import reconciled_reading as rr
from backend.services.reading_day import date_modifier

DEFAULT_WPM = 250
WPM_MIN_SECONDS = 300
PACE_WINDOW_DAYS = 30
PACE_FALLBACK_WINDOW_DAYS = 90
# A type average needs at least this many finished books before it's quoted.
TYPE_AVG_MIN_BOOKS = 2


@dataclass
class Pace:
    wpm: Optional[float]
    minutes_per_day: Optional[float]
    window_days: int = PACE_WINDOW_DAYS
    # book_type_id (None = untyped) -> (avg seconds per finished book, sample size)
    type_avg: dict[Optional[int], tuple[int, int]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "wpm": self.wpm,
            "default_wpm": DEFAULT_WPM,
            "minutes_per_day": self.minutes_per_day,
            "window_days": self.window_days,
        }


def compute_pace(db: Session, user_id: int, tz_offset: int = 0) -> Pace:
    tzm = date_modifier(tz_offset)
    covered = rr.covered_book_ids(db, user_id)

    # All-time reconciled seconds per book — feeds both wpm and type averages.
    secs_by_book = rr.book_seconds(db, user_id, tzm, covered, None, None)
    finished = (
        db.query(Book.id, Book.word_count, Book.book_type_id)
        .join(UserBookStatus, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == user_id,
            UserBookStatus.status == "read",
            Book.status == "active",
        )
        .all()
    )

    words = secs = 0
    per_type: dict[Optional[int], list[int]] = {}
    for bid, wc, type_id in finished:
        s = int(secs_by_book.get(bid, (0, 0, 0))[0])
        if s < WPM_MIN_SECONDS:
            continue
        if wc:
            words += int(wc)
            secs += s
        per_type.setdefault(type_id, []).append(s)

    wpm = round(words * 60 / secs, 1) if secs > 0 else None
    type_avg = {
        t: (sum(v) // len(v), len(v))
        for t, v in per_type.items()
        if len(v) >= TYPE_AVG_MIN_BOOKS
    }

    minutes_per_day = None
    window = PACE_WINDOW_DAYS
    for window in (PACE_WINDOW_DAYS, PACE_FALLBACK_WINDOW_DAYS):
        cutoff = datetime.utcnow() - timedelta(days=window)
        recent_secs, _, _ = rr.totals(db, user_id, tzm, covered, cutoff, None)
        if recent_secs > 0:
            minutes_per_day = round(recent_secs / 60 / window, 1)
            break

    return Pace(wpm=wpm, minutes_per_day=minutes_per_day, window_days=window, type_avg=type_avg)


def estimate_seconds(book: Book, pace: Pace) -> tuple[Optional[int], Optional[str]]:
    """(seconds, method) for one book; (None, None) when there's nothing to go on."""
    if book.word_count:
        if pace.wpm:
            return round(book.word_count / pace.wpm * 60), "words"
        return round(book.word_count / DEFAULT_WPM * 60), "default"
    avg = pace.type_avg.get(book.book_type_id)
    if avg:
        return avg[0], "type_avg"
    return None, None


def days_for(seconds: Optional[int], pace: Pace) -> Optional[float]:
    if seconds is None or not pace.minutes_per_day:
        return None
    return round(seconds / 60 / pace.minutes_per_day, 1)


def estimate_book(book: Book, pace: Pace) -> dict:
    seconds, method = estimate_seconds(book, pace)
    return {
        "seconds": seconds,
        "days": days_for(seconds, pace),
        "method": method,
        "pace": pace.as_dict(),
    }


def summarise(db: Session, books: Iterable[Book], pace: Pace) -> dict:
    """Aggregate estimate over a set of books, broken down by book type."""
    type_labels = {t.id: t.label for t in db.query(BookType.id, BookType.label).all()}

    total = estimated = unestimated = 0
    total_seconds = 0
    by_type: dict[Optional[int], dict] = {}
    for b in books:
        total += 1
        seconds, method = estimate_seconds(b, pace)
        row = by_type.setdefault(
            b.book_type_id,
            {"label": type_labels.get(b.book_type_id, "Uncategorized"), "books": 0,
             "seconds": 0, "unestimated": 0, "type_avg": 0},
        )
        row["books"] += 1
        if seconds is None:
            unestimated += 1
            row["unestimated"] += 1
            continue
        estimated += 1
        total_seconds += seconds
        row["seconds"] += seconds
        if method == "type_avg":
            row["type_avg"] += 1

    rows = sorted(by_type.values(), key=lambda r: (-r["seconds"], r["label"]))
    for r in rows:
        r["days"] = days_for(r["seconds"], pace) if r["seconds"] else None

    return {
        "books": total,
        "estimated": estimated,
        "unestimated": unestimated,
        "seconds": total_seconds,
        "days": days_for(total_seconds, pace) if estimated else None,
        "by_type": rows,
        "pace": pace.as_dict(),
    }
