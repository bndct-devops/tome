"""Home tab summary endpoints."""
from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.tome_sync import ReadingSession
from backend.models.book import Book
from backend.models.user_book_status import UserBookStatus

router = APIRouter(prefix="/home", tags=["home"])


def _calc_current_streak(dates: list[date]) -> int:
    """Return current streak in days from a list of dates with activity."""
    if not dates:
        return 0
    day_set = set(dates)
    today = date.today()
    current = 0
    d = today
    while d in day_set:
        current += 1
        d -= timedelta(days=1)
    if current == 0:
        d = today - timedelta(days=1)
        while d in day_set:
            current += 1
            d -= timedelta(days=1)
    return current


@router.get("/stats")
def get_home_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Quick stats summary for the last 30 days."""
    cutoff = datetime.utcnow() - timedelta(days=30)

    base = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.started_at >= cutoff,
        )
    )

    reading_seconds_30d: int = base.with_entities(
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0)
    ).scalar() or 0

    pages_turned_30d: int = base.with_entities(
        func.coalesce(func.sum(ReadingSession.pages_turned), 0)
    ).scalar() or 0

    books_finished_30d: int = (
        db.query(UserBookStatus)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "read",
            UserBookStatus.updated_at >= cutoff,
        )
        .count()
    )

    # Current streak (all-time reading dates)
    all_dates_rows = (
        db.query(func.date(ReadingSession.started_at).label("d"))
        .filter(ReadingSession.user_id == current_user.id)
        .distinct()
        .all()
    )
    all_dates = [date.fromisoformat(r.d) for r in all_dates_rows if r.d]
    current_streak_days = _calc_current_streak(all_dates)

    return {
        "current_streak_days": current_streak_days,
        "books_finished_30d": books_finished_30d,
        "reading_seconds_30d": reading_seconds_30d,
        "pages_turned_30d": pages_turned_30d,
    }


@router.get("/activity")
def get_home_activity(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Last 10 reading sessions with book info."""
    rows = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.book_id.isnot(None),
        )
        .join(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.book_id,
            Book.title.label("book_title"),
            Book.cover_path.label("book_cover_path"),
            ReadingSession.started_at,
            ReadingSession.duration_seconds,
            ReadingSession.pages_turned,
        )
        .order_by(ReadingSession.started_at.desc())
        .limit(10)
        .all()
    )

    return [
        {
            "book_id": r.book_id,
            "book_title": r.book_title,
            "book_cover_path": r.book_cover_path,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "duration_seconds": r.duration_seconds,
            "pages_turned": r.pages_turned,
        }
        for r in rows
    ]
