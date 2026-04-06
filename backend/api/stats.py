"""Personal reading statistics endpoint. TomeSync data only."""
from datetime import datetime, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func, Integer
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.book import Book
from backend.models.user_book_status import UserBookStatus
from backend.models.library import BookType

router = APIRouter(tags=["stats"])


def _date_range(days: int) -> Optional[datetime]:
    if days <= 0:
        return None
    return datetime.utcnow() - timedelta(days=days)


def _calc_streaks(dates: list[date]) -> tuple[int, int]:
    """Return (current_streak, longest_streak) from a list of dates with activity."""
    if not dates:
        return 0, 0
    day_set = set(dates)
    today = date.today()
    # Current streak: walk backwards from today
    current = 0
    d = today
    while d in day_set:
        current += 1
        d -= timedelta(days=1)
    # If today has no activity, check if yesterday starts the streak
    if current == 0:
        d = today - timedelta(days=1)
        while d in day_set:
            current += 1
            d -= timedelta(days=1)
    # Longest streak: walk the sorted set
    sorted_days = sorted(day_set)
    longest = 1
    run = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return current, longest


def _fill_daily(rows: list, cutoff: datetime, now: datetime) -> list[dict]:
    """Fill gaps so every day in the range has an entry."""
    row_map: dict[str, dict] = {r.date: {"seconds": r.seconds or 0, "sessions": r.sessions or 0, "pages": r.pages or 0} for r in rows}
    result = []
    d = cutoff.date() if cutoff else now.date() - timedelta(days=365)
    end = now.date()
    while d <= end:
        key = d.isoformat()
        entry = row_map.get(key, {"seconds": 0, "sessions": 0, "pages": 0})
        result.append({"date": key, **entry})
        d += timedelta(days=1)
    return result


@router.get("/stats")
def get_stats(
    days: int = Query(30, ge=0),
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    now = datetime.utcnow()
    cutoff = _date_range(days)

    # Convert JS getTimezoneOffset (minutes, negative = east of UTC) to SQLite modifier
    # e.g. CEST = UTC+2 → JS returns -120 → we need '+2 hours'
    offset_hours = -(tz_offset // 60)
    tz_modifier = f"{offset_hours:+d} hours"

    # Base query filtered to this user
    base = db.query(ReadingSession).filter(ReadingSession.user_id == current_user.id)
    if cutoff:
        base = base.filter(ReadingSession.started_at >= cutoff)

    total_seconds = base.with_entities(
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0)
    ).scalar() or 0

    total_sessions = base.count()

    pages_turned = base.with_entities(
        func.coalesce(func.sum(ReadingSession.pages_turned), 0)
    ).scalar() or 0

    avg_session = int(total_seconds / total_sessions) if total_sessions > 0 else 0

    # Books finished (within range)
    finished_query = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.status == "read",
    )
    if cutoff:
        finished_query = finished_query.filter(UserBookStatus.updated_at >= cutoff)
    books_finished_count = finished_query.count()

    # Streaks (all time)
    all_dates_rows = (
        db.query(func.date(ReadingSession.started_at, tz_modifier).label("d"))
        .filter(ReadingSession.user_id == current_user.id)
        .distinct()
        .all()
    )
    all_dates = [date.fromisoformat(r.d) for r in all_dates_rows if r.d]
    current_streak, longest_streak = _calc_streaks(all_dates)

    # Daily aggregation (for selected range)
    daily_rows = (
        base.with_entities(
            func.date(ReadingSession.started_at, tz_modifier).label("date"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by(func.date(ReadingSession.started_at, tz_modifier))
        .order_by(func.date(ReadingSession.started_at, tz_modifier))
        .all()
    )
    daily = _fill_daily(daily_rows, cutoff or (now - timedelta(days=365)), now)

    # Heatmap daily — always last 365 days
    heatmap_cutoff = now - timedelta(days=365)
    heatmap_rows = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.started_at >= heatmap_cutoff,
        )
        .with_entities(
            func.date(ReadingSession.started_at, tz_modifier).label("date"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by(func.date(ReadingSession.started_at, tz_modifier))
        .order_by(func.date(ReadingSession.started_at, tz_modifier))
        .all()
    )
    heatmap_daily = _fill_daily(heatmap_rows, heatmap_cutoff, now)

    # Books finished list (for chart)
    finished_books = (
        finished_query
        .join(Book, Book.id == UserBookStatus.book_id)
        .with_entities(
            UserBookStatus.updated_at,
            Book.id,
            Book.title,
        )
        .order_by(UserBookStatus.updated_at)
        .all()
    )
    books_finished_list = [
        {"date": row.updated_at.date().isoformat(), "book_id": row.id, "title": row.title}
        for row in finished_books
    ]

    # Top books by reading time
    top_books_rows = (
        base.filter(ReadingSession.book_id.isnot(None))
        .join(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.book_id,
            Book.title,
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by(ReadingSession.book_id, Book.title)
        .order_by(func.sum(ReadingSession.duration_seconds).desc())
        .limit(10)
        .all()
    )
    top_books = [
        {"book_id": r.book_id, "title": r.title, "seconds": r.seconds, "sessions": r.sessions}
        for r in top_books_rows
    ]

    # By category
    category_rows = (
        base.filter(ReadingSession.book_id.isnot(None))
        .join(Book, Book.id == ReadingSession.book_id)
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .with_entities(
            func.coalesce(BookType.label, "Uncategorized").label("category"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
            func.count(func.distinct(ReadingSession.book_id)).label("book_count"),
        )
        .group_by(func.coalesce(BookType.label, "Uncategorized"))
        .order_by(func.sum(ReadingSession.duration_seconds).desc())
        .all()
    )
    by_category = [
        {"category": r.category, "seconds": r.seconds, "sessions": r.sessions, "book_count": r.book_count}
        for r in category_rows
    ]

    # Hourly distribution — bucket session time into hour-of-day slots (local time)
    local_time_expr = func.datetime(ReadingSession.started_at, tz_modifier)
    hourly_rows = (
        base.with_entities(
            func.cast(func.strftime('%H', local_time_expr), Integer).label("hour"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by(func.strftime('%H', local_time_expr))
        .all()
    )
    hourly_map = {r.hour: {"seconds": r.seconds, "sessions": r.sessions} for r in hourly_rows}
    hourly = [{"hour": h, "seconds": hourly_map.get(h, {}).get("seconds", 0), "sessions": hourly_map.get(h, {}).get("sessions", 0)} for h in range(24)]

    # Weekly pattern — day-of-week aggregation (0=Sun..6=Sat in SQLite strftime('%w'))
    weekly_rows = (
        base.with_entities(
            func.cast(func.strftime('%w', local_time_expr), Integer).label("dow"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by(func.strftime('%w', local_time_expr))
        .all()
    )
    weekly_map = {r.dow: {"seconds": r.seconds, "sessions": r.sessions} for r in weekly_rows}
    # Reorder to Mon(1)..Sun(0)
    dow_order = [1, 2, 3, 4, 5, 6, 0]
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekly = [{"day": dow_labels[i], "seconds": weekly_map.get(d, {}).get("seconds", 0), "sessions": weekly_map.get(d, {}).get("sessions", 0)} for i, d in enumerate(dow_order)]

    # Reading pace — per session, pages/minute
    pace_rows = (
        base.filter(
            ReadingSession.duration_seconds > 60,
            ReadingSession.pages_turned > 0,
            ReadingSession.book_id.isnot(None),
        )
        .join(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.id,
            Book.title,
            ReadingSession.started_at,
            ReadingSession.duration_seconds,
            ReadingSession.pages_turned,
        )
        .order_by(ReadingSession.started_at.desc())
        .limit(30)
        .all()
    )
    reading_pace = [
        {
            "session_id": r.id,
            "title": r.title,
            "date": r.started_at.date().isoformat() if r.started_at else None,
            "pages_per_min": round(r.pages_turned / (r.duration_seconds / 60), 2) if r.duration_seconds else 0,
            "duration_seconds": r.duration_seconds,
            "pages_turned": r.pages_turned,
        }
        for r in pace_rows
    ]

    # Books in progress — currently reading with progress
    in_progress_rows = (
        db.query(UserBookStatus)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "reading",
        )
        .join(Book, Book.id == UserBookStatus.book_id)
        .outerjoin(TomeSyncPosition, (TomeSyncPosition.book_id == Book.id) & (TomeSyncPosition.user_id == current_user.id))
        .with_entities(
            Book.id,
            Book.title,
            Book.author,
            Book.cover_path,
            func.coalesce(TomeSyncPosition.percentage, UserBookStatus.progress_pct, 0.0).label("progress"),
            UserBookStatus.updated_at,
        )
        .order_by(UserBookStatus.updated_at.desc())
        .all()
    )
    books_in_progress = [
        {
            "book_id": r.id,
            "title": r.title,
            "author": r.author,
            "has_cover": bool(r.cover_path),
            "progress": round(r.progress * 100, 1) if r.progress and r.progress <= 1 else round(r.progress, 1) if r.progress else 0,
            "last_read": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in in_progress_rows
    ]

    # Session timeline — recent sessions with start/end times for timeline view
    timeline_rows = (
        base.filter(
            ReadingSession.started_at.isnot(None),
            ReadingSession.ended_at.isnot(None),
            ReadingSession.book_id.isnot(None),
        )
        .join(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.id,
            ReadingSession.started_at,
            ReadingSession.ended_at,
            ReadingSession.duration_seconds,
            Book.title,
        )
        .order_by(ReadingSession.started_at.desc())
        .limit(50)
        .all()
    )
    session_timeline = [
        {
            "id": r.id,
            "title": r.title,
            "started_at": r.started_at.isoformat() + "Z",
            "ended_at": r.ended_at.isoformat() + "Z",
            "duration_seconds": r.duration_seconds,
        }
        for r in timeline_rows
    ]

    # ── Period comparison ─────────────────────────────────────────────────────
    period_comparison = None
    if days > 0:
        start_date = cutoff  # already computed above
        prev_start = start_date - timedelta(days=days)
        prev_seconds = db.query(
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0)
        ).filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.started_at >= prev_start,
            ReadingSession.started_at < start_date,
        ).scalar() or 0
        pct_change: Optional[float] = 0.0
        if prev_seconds > 0:
            pct_change = round(((total_seconds - prev_seconds) / prev_seconds) * 100, 1)
        elif total_seconds > 0:
            # Current period has data but previous has none — not a meaningful percentage
            pct_change = None
        period_comparison = {
            "current_seconds": total_seconds,
            "previous_seconds": int(prev_seconds),
            "pct_change": pct_change,
        }

    # ── Year summary ──────────────────────────────────────────────────────────
    year_summary = None
    if days >= 365 or days == 0:
        # Top genre from finished books
        top_genre: Optional[str] = None
        finished_book_ids = [row.id for row in finished_books]
        if finished_book_ids:
            genre_row = (
                db.query(
                    func.coalesce(BookType.label, "Uncategorized").label("genre"),
                    func.count(Book.id).label("cnt"),
                )
                .select_from(Book)
                .filter(Book.id.in_(finished_book_ids))
                .outerjoin(BookType, BookType.id == Book.book_type_id)
                .group_by(func.coalesce(BookType.label, "Uncategorized"))
                .order_by(func.count(Book.id).desc())
                .first()
            )
            if genre_row:
                top_genre = genre_row.genre

        # Most active month from daily data
        most_active_month: Optional[str] = None
        if daily:
            month_secs: dict[str, int] = {}
            for entry in daily:
                if entry["seconds"] > 0:
                    month_key = entry["date"][:7]  # e.g. "2024-03"
                    month_secs[month_key] = month_secs.get(month_key, 0) + entry["seconds"]
            if month_secs:
                best_month_key = max(month_secs, key=lambda k: month_secs[k])
                # Parse to month name: "2024-03" → "March"
                try:
                    most_active_month = datetime.strptime(best_month_key, "%Y-%m").strftime("%B")
                except ValueError:
                    most_active_month = None

        year_summary = {
            "books_finished": books_finished_count,
            "total_hours": round(total_seconds / 3600, 1),
            "top_genre": top_genre,
            "longest_streak_days": longest_streak,
            "total_sessions": total_sessions,
            "most_active_month": most_active_month,
        }

    # ── Per-book time breakdown (full list, no limit) ────────────────────────
    per_book_rows = (
        base.filter(ReadingSession.book_id.isnot(None))
        .join(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.book_id,
            Book.title,
            Book.author,
            Book.cover_path,
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages_turned"),
        )
        .group_by(ReadingSession.book_id, Book.title, Book.author, Book.cover_path)
        .order_by(func.sum(ReadingSession.duration_seconds).desc())
        .all()
    )
    per_book_time = [
        {
            "book_id": r.book_id,
            "title": r.title,
            "author": r.author,
            "has_cover": bool(r.cover_path),
            "seconds": r.seconds,
            "sessions": r.sessions,
            "pages_turned": r.pages_turned,
        }
        for r in per_book_rows
    ]

    # ── Monthly comparison (last 12 months) ───────────────────────────────
    month_cutoff = now - timedelta(days=365)
    month_expr = func.strftime('%Y-%m', func.datetime(ReadingSession.started_at, tz_modifier))

    monthly_session_rows = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.started_at >= month_cutoff,
        )
        .with_entities(
            month_expr.label("month"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by(month_expr)
        .all()
    )
    month_session_map: dict[str, dict] = {
        r.month: {"seconds": int(r.seconds), "sessions": int(r.sessions)}
        for r in monthly_session_rows
    }

    # Books finished per month
    month_finished_rows = (
        db.query(
            func.strftime('%Y-%m', UserBookStatus.updated_at).label("month"),
            func.count(UserBookStatus.id).label("cnt"),
        )
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "read",
            UserBookStatus.updated_at >= month_cutoff,
        )
        .group_by(func.strftime('%Y-%m', UserBookStatus.updated_at))
        .all()
    )
    month_finished_map = {r.month: int(r.cnt) for r in month_finished_rows}

    # Build 12-month list
    monthly_comparison = []
    for i in range(11, -1, -1):
        d = now - timedelta(days=i * 30)
        month_key = d.strftime("%Y-%m")
        label = d.strftime("%b")
        sdata = month_session_map.get(month_key, {"seconds": 0, "sessions": 0})
        monthly_comparison.append({
            "month": month_key,
            "label": label,
            "books_finished": month_finished_map.get(month_key, 0),
            "reading_hours": round(sdata["seconds"] / 3600, 1),
            "sessions": sdata["sessions"],
            "reading_seconds": sdata["seconds"],
        })

    # ── Genre over time (last 12 months, stacked) ─────────────────────────
    genre_month_expr = func.strftime('%Y-%m', func.datetime(ReadingSession.started_at, tz_modifier))
    genre_time_rows = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == current_user.id,
            ReadingSession.started_at >= month_cutoff,
            ReadingSession.book_id.isnot(None),
        )
        .join(Book, Book.id == ReadingSession.book_id)
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .with_entities(
            genre_month_expr.label("month"),
            func.coalesce(BookType.label, "Uncategorized").label("category"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
        )
        .group_by(genre_month_expr, func.coalesce(BookType.label, "Uncategorized"))
        .all()
    )

    # Pivot: each row = { month, Cat1: secs, Cat2: secs, ... }
    genre_month_map: dict[str, dict[str, int]] = {}
    all_categories: set[str] = set()
    for r in genre_time_rows:
        if r.month not in genre_month_map:
            genre_month_map[r.month] = {}
        genre_month_map[r.month][r.category] = int(r.seconds)
        all_categories.add(r.category)

    genre_over_time = []
    for i in range(11, -1, -1):
        d = now - timedelta(days=i * 30)
        month_key = d.strftime("%Y-%m")
        entry: dict[str, int | str] = {"month": month_key}
        cat_data = genre_month_map.get(month_key, {})
        for cat in sorted(all_categories):
            entry[cat] = cat_data.get(cat, 0)
        genre_over_time.append(entry)

    return {
        "range_days": days,
        "headline": {
            "total_reading_seconds": total_seconds,
            "total_sessions": total_sessions,
            "books_finished": books_finished_count,
            "avg_session_seconds": avg_session,
            "current_streak_days": current_streak,
            "longest_streak_days": longest_streak,
            "pages_turned": pages_turned,
        },
        "daily": daily,
        "heatmap_daily": heatmap_daily,
        "books_finished": books_finished_list,
        "top_books": top_books,
        "by_category": by_category,
        "hourly": hourly,
        "weekly": weekly,
        "reading_pace": reading_pace,
        "books_in_progress": books_in_progress,
        "session_timeline": session_timeline,
        "year_summary": year_summary,
        "period_comparison": period_comparison,
        "per_book_time": per_book_time,
        "monthly_comparison": monthly_comparison,
        "genre_over_time": genre_over_time,
    }


@router.get("/stats/completion-estimates")
def get_completion_estimates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """Estimate days remaining for each book the user is currently reading."""
    window_start = datetime.utcnow() - timedelta(days=30)

    in_progress = (
        db.query(UserBookStatus)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "reading",
        )
        .join(Book, Book.id == UserBookStatus.book_id)
        .outerjoin(
            TomeSyncPosition,
            (TomeSyncPosition.book_id == Book.id) & (TomeSyncPosition.user_id == current_user.id),
        )
        .with_entities(
            Book.id,
            Book.title,
            Book.author,
            Book.cover_path,
            func.coalesce(TomeSyncPosition.percentage, UserBookStatus.progress_pct, 0.0).label("progress_raw"),
        )
        .all()
    )

    result = []
    for row in in_progress:
        # Normalise progress to 0–100
        p = row.progress_raw or 0.0
        progress = round(p * 100, 1) if p <= 1.0 else round(p, 1)

        # Sessions for this book in the last 30 days
        session_rows = (
            db.query(ReadingSession)
            .filter(
                ReadingSession.user_id == current_user.id,
                ReadingSession.book_id == row.id,
                ReadingSession.started_at >= window_start,
            )
            .with_entities(
                func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_secs"),
                func.count(ReadingSession.id).label("session_count"),
                func.min(ReadingSession.started_at).label("first_session"),
                func.min(ReadingSession.progress_start).label("earliest_progress"),
            )
            .first()
        )

        total_secs_30 = int(session_rows.total_secs) if session_rows and session_rows.total_secs else 0
        session_count = int(session_rows.session_count) if session_rows and session_rows.session_count else 0

        estimated_days: Optional[int] = None
        if session_count > 0 and progress > 0 and progress < 100:
            # Calculate progress gained during the window
            earliest_pct = session_rows.earliest_progress or 0.0
            # Normalise earliest_pct the same way as progress (0-1 → 0-100)
            earliest_pct = round(earliest_pct * 100, 1) if earliest_pct <= 1.0 else round(earliest_pct, 1)
            progress_gained = max(progress - earliest_pct, 0.1)  # floor to avoid div-by-zero

            # Use actual days elapsed since first session, not fixed 30
            days_elapsed = max(1, (datetime.utcnow() - session_rows.first_session).days) if session_rows.first_session else 30
            progress_per_day = progress_gained / days_elapsed
            remaining = 100.0 - progress
            estimated_days = max(1, round(remaining / progress_per_day))

        if session_count >= 5:
            confidence = "high"
        elif session_count >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        result.append({
            "book_id": row.id,
            "title": row.title,
            "author": row.author,
            "has_cover": bool(row.cover_path),
            "progress": progress,
            "estimated_days": estimated_days,
            "confidence": confidence,
        })

    # Sort by progress descending (closest to finishing first)
    result.sort(key=lambda x: x["progress"], reverse=True)
    return result


@router.get("/stats/sessions")
def list_sessions(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List individual reading sessions for the current user, newest first."""
    base = (
        db.query(ReadingSession)
        .filter(ReadingSession.user_id == current_user.id)
    )
    total = base.count()
    rows = (
        base
        .outerjoin(Book, Book.id == ReadingSession.book_id)
        .with_entities(
            ReadingSession.id,
            ReadingSession.book_id,
            Book.title.label("book_title"),
            ReadingSession.started_at,
            ReadingSession.ended_at,
            ReadingSession.duration_seconds,
            ReadingSession.pages_turned,
            ReadingSession.device,
            ReadingSession.progress_start,
            ReadingSession.progress_end,
        )
        .order_by(ReadingSession.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "sessions": [
            {
                "id": r.id,
                "book_id": r.book_id,
                "book_title": r.book_title or "(deleted book)",
                "started_at": (r.started_at.isoformat() + "Z") if r.started_at else None,
                "ended_at": (r.ended_at.isoformat() + "Z") if r.ended_at else None,
                "duration_seconds": r.duration_seconds,
                "pages_turned": r.pages_turned,
                "device": r.device,
                "progress_start": r.progress_start,
                "progress_end": r.progress_end,
            }
            for r in rows
        ],
    }


@router.delete("/stats/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a single reading session owned by the current user."""
    session = (
        db.query(ReadingSession)
        .filter(ReadingSession.id == session_id, ReadingSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return {"ok": True}
