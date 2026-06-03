"""Reusable reading-statistics aggregation over ReadingSession records.

Used by:
  - GET /books/{book_id}/reading-stats  (per-book stats, Step 1)
  - Future: per-series stats (Step 2)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus


# ── Per-book, per-user ────────────────────────────────────────────────────────

def compute_book_reading_stats(
    db: Session,
    *,
    user_id: int,
    book_id: int,
) -> dict:
    """Return reading statistics for one user on one book.

    Returns a dict with keys:
      total_seconds, sessions, pages_turned, avg_session_seconds,
      pace_pages_per_min, first_read, last_read, progress, status,
      session_timeline, estimated_finish_seconds
    """
    base = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == user_id,
            ReadingSession.book_id == book_id,
        )
    )

    # ── Aggregate totals ─────────────────────────────────────────────────────
    agg = base.with_entities(
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_seconds"),
        func.count(ReadingSession.id).label("sessions"),
        func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages_turned"),
        func.min(ReadingSession.started_at).label("first_read"),
        func.max(
            func.coalesce(ReadingSession.ended_at, ReadingSession.started_at)
        ).label("last_read"),
    ).first()

    total_seconds: int = int(agg.total_seconds) if agg and agg.total_seconds else 0
    sessions: int = int(agg.sessions) if agg and agg.sessions else 0
    pages_turned: int = int(agg.pages_turned) if agg and agg.pages_turned else 0

    avg_session_seconds: int = (
        round(total_seconds / sessions) if sessions > 0 else 0
    )

    # Pace: pages per minute
    total_minutes = total_seconds / 60.0
    if total_minutes > 0 and pages_turned > 0:
        pace_pages_per_min: Optional[float] = round(pages_turned / total_minutes, 2)
    else:
        pace_pages_per_min = None

    first_read: Optional[str] = (
        agg.first_read.isoformat() + "Z" if agg and agg.first_read else None
    )
    last_read: Optional[str] = (
        agg.last_read.isoformat() + "Z" if agg and agg.last_read else None
    )

    # ── Reading status + progress ────────────────────────────────────────────
    status_row = (
        db.query(UserBookStatus)
        .filter_by(user_id=user_id, book_id=book_id)
        .first()
    )
    book_status: str = status_row.status if status_row else "unread"
    # progress_pct is stored as 0-1 fraction in UserBookStatus
    progress: Optional[float] = status_row.progress_pct if status_row else None

    # ── Session timeline — daily buckets ─────────────────────────────────────
    timeline_rows = (
        base.with_entities(
            func.date(ReadingSession.started_at).label("date"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by(func.date(ReadingSession.started_at))
        .order_by(func.date(ReadingSession.started_at))
        .all()
    )
    session_timeline = [
        {"date": row.date, "seconds": int(row.seconds), "pages": int(row.pages)}
        for row in timeline_rows
    ]

    # ── Estimated time to finish ─────────────────────────────────────────────
    estimated_finish_seconds: Optional[int] = None
    if (
        progress is not None
        and 0 < progress < 1
        and total_seconds > 0
    ):
        # T/p*(1-p): at current pace, how many more seconds remain?
        estimated_finish_seconds = round(total_seconds / progress * (1 - progress))

    return {
        "total_seconds": total_seconds,
        "sessions": sessions,
        "pages_turned": pages_turned,
        "avg_session_seconds": avg_session_seconds,
        "pace_pages_per_min": pace_pages_per_min,
        "first_read": first_read,
        "last_read": last_read,
        "progress": progress,
        "status": book_status,
        "session_timeline": session_timeline,
        "estimated_finish_seconds": estimated_finish_seconds,
    }


# ── Admin aggregate — all users, one book ────────────────────────────────────

def compute_book_aggregate_stats(
    db: Session,
    *,
    book_id: int,
) -> dict:
    """Return library-wide reading statistics for one book (all users combined).

    Returns a dict with keys:
      total_seconds, total_sessions, distinct_readers
    """
    agg = (
        db.query(ReadingSession)
        .filter(ReadingSession.book_id == book_id)
        .with_entities(
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_seconds"),
            func.count(ReadingSession.id).label("total_sessions"),
            func.count(func.distinct(ReadingSession.user_id)).label("distinct_readers"),
        )
        .first()
    )

    return {
        "total_seconds": int(agg.total_seconds) if agg and agg.total_seconds else 0,
        "total_sessions": int(agg.total_sessions) if agg and agg.total_sessions else 0,
        "distinct_readers": int(agg.distinct_readers) if agg and agg.distinct_readers else 0,
    }
