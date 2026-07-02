"""Home tab summary endpoints."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.permissions import book_visibility_filter
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.book import Book
from backend.models.user_book_status import UserBookStatus
from backend.services.streaks import reconciled_user_streaks

router = APIRouter(prefix="/home", tags=["home"])


def _norm_pct(raw: float | None) -> float:
    """Normalise a progress value (KOReader 0–1 or legacy 0–100) to 0–100.

    Everything written since the web reader/TomeSync convention is a 0–1
    fraction, so <= 1.0 means "fraction". A legacy 0–100 row that happened to
    sit below 1% is misread as 80–100% — unavoidable ambiguity, accepted as
    vanishingly rare.
    """
    if not raw:
        return 0.0
    return round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)


@router.get("/focus")
def get_focus(
    book_id: int | None = Query(None, description="Focus a specific in-progress book instead of the latest-synced one"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The single book to surface in Home's Focus mode: the user's most-recently
    active in-progress title (latest sync wins), plus the upcoming volumes of its
    series fanned behind it, and the rest of the in-progress set as a switcher.

    When ``book_id`` is given and it's one of the user's visible in-progress books,
    that book becomes the hero instead (used by the "Also reading" switcher).
    """
    visible = book_visibility_filter(db, current_user)

    rows = (
        db.query(UserBookStatus)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "reading",
        )
        .join(Book, Book.id == UserBookStatus.book_id)
        .filter(Book.status == "active", visible)
        .outerjoin(
            TomeSyncPosition,
            (TomeSyncPosition.book_id == Book.id)
            & (TomeSyncPosition.user_id == current_user.id),
        )
        .with_entities(
            Book.id,
            Book.title,
            Book.author,
            Book.series,
            Book.series_index,
            Book.description,
            Book.cover_path,
            TomeSyncPosition.percentage.label("sync_pct"),
            TomeSyncPosition.device.label("device"),
            TomeSyncPosition.updated_at.label("synced_at"),
            UserBookStatus.progress_pct,
            UserBookStatus.updated_at.label("status_at"),
        )
        .all()
    )

    if not rows:
        return {"ready": False, "book": None, "upcoming": [], "ahead_count": 0, "reading": []}

    # Most-recent activity = the newer of (last sync, last status change).
    def last_active(r):
        candidates = [t for t in (r.synced_at, r.status_at) if t is not None]
        return max(candidates) if candidates else datetime.min

    # Canonical, stable order (latest activity first). This order does NOT depend
    # on which book is focused, so the "Currently reading" strip stays static when
    # the user switches the hero — only the active highlight moves.
    ordered = sorted(rows, key=last_active, reverse=True)
    # The switcher can pin a specific book as hero; otherwise latest activity wins.
    hero = ordered[0]
    if book_id is not None:
        hero = next((r for r in ordered if r.id == book_id), ordered[0])

    progress = _norm_pct(hero.sync_pct if hero.sync_pct is not None else hero.progress_pct)
    synced_at = hero.synced_at or hero.status_at

    # Upcoming volumes of this series the user can see (only if it's a real series).
    upcoming: list[dict] = []
    ahead_count = 0
    if hero.series and hero.series_index is not None:
        vol_rows = (
            db.query(Book)
            .filter(
                Book.status == "active",
                Book.series == hero.series,
                Book.series_index > hero.series_index,
                visible,
            )
            .order_by(Book.series_index.asc())
            .with_entities(Book.id, Book.title, Book.series_index, Book.cover_path, Book.description)
            .all()
        )
        # Whole volumes only — exclude side-story / fractional indices (e.g. 13.5)
        # so "N ahead" counts the main line, not bonus material.
        vol_rows = [v for v in vol_rows if v.series_index is not None and float(v.series_index).is_integer()]
        ahead_count = len(vol_rows)
        upcoming = [
            {
                "book_id": v.id,
                "title": v.title,
                "series_index": v.series_index,
                "has_cover": bool(v.cover_path),
                "description": v.description,
            }
            for v in vol_rows[:5]
        ]

    # The full in-progress set, in stable order, INCLUDING the current hero — the
    # strip is a static filmstrip; the frontend just highlights whichever is active.
    reading = [
        {
            "book_id": r.id,
            "title": r.title,
            "author": r.author,
            "has_cover": bool(r.cover_path),
            "progress": _norm_pct(r.sync_pct if r.sync_pct is not None else r.progress_pct),
        }
        for r in ordered[:12]
    ]

    return {
        "ready": True,
        # Books 13+ can't be focused from the strip — the frontend shows a
        # "+N more" link into the filtered library instead of hiding them.
        "reading_total": len(ordered),
        "book": {
            "book_id": hero.id,
            "title": hero.title,
            "author": hero.author,
            "series": hero.series,
            "series_index": hero.series_index,
            "description": hero.description,
            "has_cover": bool(hero.cover_path),
            "progress": progress,
            "device": hero.device,
            "synced": hero.synced_at is not None,
            "last_sync": synced_at.isoformat() + "Z" if synced_at else None,
        },
        "upcoming": upcoming,
        "ahead_count": ahead_count,
        "reading": reading,
    }


@router.get("/stats")
def get_home_stats(
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
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

    # Reconciled so the home streak matches the stats page: imported KOReader
    # page-stat days count alongside live sessions (no-op without imported stats).
    current_streak_days, _ = reconciled_user_streaks(db, current_user.id, tz_offset)

    return {
        "current_streak_days": current_streak_days,
        "books_finished_30d": books_finished_30d,
        "reading_seconds_30d": reading_seconds_30d,
        "pages_turned_30d": pages_turned_30d,
    }


@router.get("/reading-dna")
def get_home_reading_dna(
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Reading-personality summary for the Home rail (see services.reading_dna)."""
    from backend.services.reading_dna import compute_reading_dna

    return compute_reading_dna(db, current_user, tz_offset)


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
            "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
            "duration_seconds": r.duration_seconds,
            "pages_turned": r.pages_turned,
        }
        for r in rows
    ]


@router.get("/forgotten-books")
def forgotten_books(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Return books marked 'reading' that haven't been touched in 30+ days."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    rows = (
        db.query(UserBookStatus, Book)
        .join(Book, Book.id == UserBookStatus.book_id)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "reading",
            UserBookStatus.updated_at < cutoff,
            Book.status == "active",
        )
        .order_by(UserBookStatus.updated_at.asc())
        .limit(6)
        .all()
    )
    return [
        {
            "book_id": book.id,
            "title": book.title,
            "author": book.author,
            "has_cover": bool(book.cover_path),
            "last_read": status.updated_at.isoformat() + "Z" if status.updated_at else None,
            "days_ago": (datetime.utcnow() - status.updated_at).days if status.updated_at else None,
        }
        for status, book in rows
    ]
