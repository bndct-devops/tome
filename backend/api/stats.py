"""Personal reading statistics endpoint. TomeSync data only."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func, case, Integer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.permissions import book_visibility_filter
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.user_dashboard import UserDashboard
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.book import Book, BookFile
from backend.models.user_book_status import UserBookStatus
from backend.models.user_series_rating import UserSeriesRating
from backend.models.library import BookType
from backend.services.streaks import reconciled_user_streaks
from backend.services import reconciled_reading as rr

router = APIRouter(tags=["stats"])


def _date_range(days: int) -> Optional[datetime]:
    if days <= 0:
        return None
    return datetime.utcnow() - timedelta(days=days)


def _fill_daily(rows: list, start_date, end_date) -> list[dict]:
    """Fill gaps so every day in [start_date, end_date] has an entry."""
    row_map: dict[str, dict] = {r.date: {"seconds": r.seconds or 0, "sessions": r.sessions or 0, "pages": r.pages or 0} for r in rows}
    return _fill_daily_map(row_map, start_date, end_date)


def _fill_daily_map(day_map: dict, start_date, end_date) -> list[dict]:
    """Fill gaps from a {day_str: dict_or_(secs,sessions,pages)} map."""
    result = []
    d = start_date
    while d <= end_date:
        key = d.isoformat()
        v = day_map.get(key)
        if v is None:
            entry = {"seconds": 0, "sessions": 0, "pages": 0}
        elif isinstance(v, dict):
            entry = {"seconds": v.get("seconds", 0), "sessions": v.get("sessions", 0), "pages": v.get("pages", 0)}
        else:  # tuple (seconds, sessions, pages)
            entry = {"seconds": v[0], "sessions": v[1], "pages": v[2]}
        result.append({"date": key, **entry})
        d += timedelta(days=1)
    return result


@router.get("/stats")
def get_stats(
    days: int = Query(30, ge=0),
    start: Optional[str] = Query(None, description="Custom range start (YYYY-MM-DD, local). Overrides `days`."),
    end: Optional[str] = Query(None, description="Custom range end (YYYY-MM-DD, local, inclusive)."),
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    now = datetime.utcnow()

    # Range resolution: an explicit `start` switches to a custom date range and
    # overrides `days`. Dates are local; convert to UTC for filtering (UTC = local +
    # tz_offset) but keep the local dates for the daily-fill window, since the daily
    # rows are bucketed by local date.
    range_end: Optional[datetime] = None  # exclusive upper bound, UTC
    fill_start_local = None
    fill_end_local = None
    if start:
        try:
            sdate = datetime.strptime(start, "%Y-%m-%d")
            cutoff = sdate + timedelta(minutes=tz_offset)
            fill_start_local = sdate.date()
        except ValueError:
            cutoff = _date_range(days)
        else:
            if end:
                try:
                    edate = datetime.strptime(end, "%Y-%m-%d")
                    range_end = edate + timedelta(days=1) + timedelta(minutes=tz_offset)
                    fill_end_local = edate.date()
                except ValueError:
                    range_end = None
    else:
        cutoff = _date_range(days)

    # Effective span (days) for period-comparison + year-summary gating.
    if cutoff is not None:
        effective_days = max(1, ((range_end or now) - cutoff).days)
    else:
        effective_days = 0  # all-time

    # Inclusive fill window for the daily chart.
    fill_start = fill_start_local or (cutoff or (now - timedelta(days=365))).date()
    fill_end = fill_end_local or now.date()

    # Convert JS getTimezoneOffset (minutes, negative = east of UTC) to SQLite modifier
    # e.g. CEST = UTC+2 → JS returns -120 → we need '+2 hours'
    offset_hours = -(tz_offset // 60)
    tz_modifier = f"{offset_hours:+d} hours"

    # Base query filtered to this user (still used by session-level tiles: pace, timeline,
    # pace-by-format — page-stats have no natural "sessions" so those stay session-sourced).
    base = db.query(ReadingSession).filter(ReadingSession.user_id == current_user.id)
    if cutoff:
        base = base.filter(ReadingSession.started_at >= cutoff)
    if range_end:
        base = base.filter(ReadingSession.started_at < range_end)

    # Books whose time comes from imported KOReader page-stats (those win; their live
    # sessions are excluded to avoid double-counting). Empty -> identical to session-only.
    covered = rr.covered_book_ids(db, current_user.id)

    total_seconds, total_sessions, pages_turned = rr.totals(
        db, current_user.id, tz_modifier, covered, cutoff, range_end
    )

    avg_session = int(total_seconds / total_sessions) if total_sessions > 0 else 0

    # Books finished (within range)
    finished_query = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.status == "read",
    )
    if cutoff:
        finished_query = finished_query.filter(UserBookStatus.updated_at >= cutoff)
    if range_end:
        finished_query = finished_query.filter(UserBookStatus.updated_at < range_end)
    books_finished_count = finished_query.count()

    # Streaks (all time, local-day with 4h rollover). Reconciled: page-stat days count too.
    current_streak, longest_streak = reconciled_user_streaks(
        db, current_user.id, tz_offset, covered
    )

    # Daily aggregation (for selected range) — reconciled (page-stats win per book).
    daily = _fill_daily_map(
        rr.daily_map(db, current_user.id, tz_modifier, covered, cutoff, range_end),
        fill_start, fill_end,
    )

    # Heatmap daily — always last 365 days — reconciled.
    heatmap_cutoff = now - timedelta(days=365)
    heatmap_daily = _fill_daily_map(
        rr.daily_map(db, current_user.id, tz_modifier, covered, heatmap_cutoff, None),
        heatmap_cutoff.date(), now.date(),
    )

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

    # Reconciled per-book reading time for the window (page-stats win per book), reused by
    # top-books, category, per-book table, and author-affinity below.
    win_book = rr.book_seconds(db, current_user.id, tz_modifier, covered, cutoff, range_end)
    book_meta: dict[int, tuple] = {}
    if win_book:
        for bid, title, author, cover, label in (
            db.query(Book.id, Book.title, Book.author, Book.cover_path,
                     func.coalesce(BookType.label, "Uncategorized"))
            .outerjoin(BookType, BookType.id == Book.book_type_id)
            .filter(Book.id.in_(list(win_book.keys())))
        ):
            book_meta[bid] = (title, author, cover, label)

    def _bsorted():
        return sorted(win_book.items(), key=lambda kv: kv[1][0], reverse=True)

    # Top books by reading time
    top_books = [
        {"book_id": bid, "title": book_meta.get(bid, (None,))[0], "seconds": v[0], "sessions": v[1]}
        for bid, v in _bsorted()[:10]
    ]

    # By category — roll the reconciled per-book time up to book-type.
    _cat: dict[str, list[int]] = {}
    for bid, v in win_book.items():
        label = book_meta.get(bid, (None, None, None, "Uncategorized"))[3] or "Uncategorized"
        e = _cat.setdefault(label, [0, 0, 0])
        e[0] += v[0]; e[1] += v[1]; e[2] += 1
    by_category = [
        {"category": c, "seconds": e[0], "sessions": e[1], "book_count": e[2]}
        for c, e in sorted(_cat.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

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
            "last_read": r.updated_at.isoformat() + "Z" if r.updated_at else None,
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
    if cutoff is not None:
        start_date = cutoff
        duration = (range_end or now) - cutoff
        prev_start = start_date - duration
        prev_seconds = rr.totals(
            db, current_user.id, tz_modifier, covered, prev_start, start_date
        )[0]
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
    if cutoff is None or effective_days >= 365:
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

    # ── Per-book time breakdown (full list, no limit) — reconciled ───────────
    per_book_time = [
        {
            "book_id": bid,
            "title": book_meta.get(bid, (None, None, None, None))[0],
            "author": book_meta.get(bid, (None, None, None, None))[1],
            "has_cover": bool(book_meta.get(bid, (None, None, None, None))[2]),
            "seconds": v[0],
            "sessions": v[1],
            "pages_turned": v[2],
        }
        for bid, v in _bsorted()
    ]

    # ── Monthly comparison (last 12 months) ── reconciled ──────────────────
    month_cutoff = now - timedelta(days=365)
    _mm = rr.monthly_map(db, current_user.id, tz_modifier, covered, month_cutoff)
    month_session_map: dict[str, dict] = {
        m: {"seconds": v[0], "sessions": v[1]} for m, v in _mm.items()
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

    # ── Genre over time (last 12 months, stacked) ── reconciled ───────────
    # Reconciled per-(book, month) seconds, rolled up to book-type per month.
    _bm = rr.book_month_seconds(db, current_user.id, tz_modifier, covered, month_cutoff)
    _genre_ids = {bid for (bid, _m) in _bm.keys()}
    _genre_cat: dict[int, str] = {}
    if _genre_ids:
        for bid, label in (
            db.query(Book.id, func.coalesce(BookType.label, "Uncategorized"))
            .outerjoin(BookType, BookType.id == Book.book_type_id)
            .filter(Book.id.in_(list(_genre_ids)))
        ):
            _genre_cat[bid] = label or "Uncategorized"
    genre_month_map: dict[str, dict[str, int]] = {}
    all_categories: set[str] = set()
    for (bid, m), secs in _bm.items():
        cat = _genre_cat.get(bid, "Uncategorized")
        genre_month_map.setdefault(m, {})
        genre_month_map[m][cat] = genre_month_map[m].get(cat, 0) + secs
        all_categories.add(cat)

    genre_over_time = []
    for i in range(11, -1, -1):
        d = now - timedelta(days=i * 30)
        month_key = d.strftime("%Y-%m")
        entry: dict[str, int | str] = {"month": month_key}
        cat_data = genre_month_map.get(month_key, {})
        for cat in sorted(all_categories):
            entry[cat] = cat_data.get(cat, 0)
        genre_over_time.append(entry)

    # ── Hour × day-of-week heatmap (168 cells) ── reconciled ─────────────────
    hour_dow_map = rr.hour_dow(db, current_user.id, tz_modifier, covered, cutoff, range_end)
    hour_dow_heatmap = [
        {
            "dow": d,
            "hour": h,
            "seconds": hour_dow_map.get((d, h), (0, 0))[0],
            "sessions": hour_dow_map.get((d, h), (0, 0))[1],
        }
        for d in range(7) for h in range(24)
    ]

    # ── Series completion ─────────────────────────────────────────────────────
    series_rows = (
        db.query(
            Book.series.label("series"),
            func.count(Book.id).label("total"),
            func.sum(
                case((UserBookStatus.status == "read", 1), else_=0)
            ).label("read_count"),
            func.sum(
                case((UserBookStatus.status == "reading", 1), else_=0)
            ).label("reading_count"),
            func.min(Book.id).label("sample_book_id"),
        )
        .outerjoin(
            UserBookStatus,
            (UserBookStatus.book_id == Book.id)
            & (UserBookStatus.user_id == current_user.id),
        )
        .filter(
            Book.status == "active",
            book_visibility_filter(db, current_user),
            Book.series.isnot(None),
            Book.series != "",
        )
        .group_by(Book.series)
        .having(
            func.sum(case((UserBookStatus.status.in_(("read", "reading")), 1), else_=0)) > 0
        )
        .order_by(func.max(UserBookStatus.updated_at).desc().nullslast())
        .all()
    )
    series_completion = [
        {
            "series": r.series,
            "total": int(r.total),
            "read": int(r.read_count or 0),
            "reading": int(r.reading_count or 0),
            "pct": round((int(r.read_count or 0) / int(r.total)) * 100, 1) if r.total else 0,
            "sample_book_id": r.sample_book_id,
        }
        for r in series_rows
    ]

    # ── Author affinity ── reconciled: roll per-book time up to author ──────────
    finished_by_author = dict(
        db.query(Book.author, func.count(Book.id))
        .join(UserBookStatus, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "read",
        )
        .group_by(Book.author)
        .all()
    )
    _auth: dict[str, list[int]] = {}
    for bid, v in win_book.items():
        author = (book_meta.get(bid, (None, None))[1] or "").strip()
        if not author:
            continue
        e = _auth.setdefault(author, [0, 0, 0])
        e[0] += v[0]; e[1] += v[1]; e[2] += 1
    author_affinity = [
        {
            "author": a,
            "seconds": e[0],
            "sessions": e[1],
            "book_count": e[2],
            "books_finished": int(finished_by_author.get(a, 0)),
        }
        for a, e in sorted(_auth.items(), key=lambda kv: kv[1][0], reverse=True)[:10]
    ]

    # ── Completion rate ───────────────────────────────────────────────────────
    touched_q = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.status.in_(("reading", "read")),
    )
    started_count = touched_q.count()
    finished_count_all = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == current_user.id,
        UserBookStatus.status == "read",
    ).count()
    completion_rate = {
        "started": started_count,
        "finished": finished_count_all,
        "pct": round((finished_count_all / started_count) * 100, 1) if started_count else 0.0,
    }

    completion_by_type_rows = (
        db.query(
            func.coalesce(BookType.label, "Uncategorized").label("category"),
            func.sum(case((UserBookStatus.status.in_(("reading", "read")), 1), else_=0)).label("started"),
            func.sum(case((UserBookStatus.status == "read", 1), else_=0)).label("finished"),
        )
        .select_from(UserBookStatus)
        .join(Book, Book.id == UserBookStatus.book_id)
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .filter(UserBookStatus.user_id == current_user.id)
        .group_by(func.coalesce(BookType.label, "Uncategorized"))
        .all()
    )
    completion_by_type = [
        {
            "category": r.category,
            "started": int(r.started or 0),
            "finished": int(r.finished or 0),
            "pct": round((int(r.finished or 0) / int(r.started or 0)) * 100, 1) if (r.started or 0) else 0.0,
        }
        for r in completion_by_type_rows
        if (r.started or 0) > 0
    ]

    # ── Pace by format ────────────────────────────────────────────────────────
    pace_format_rows = (
        base.filter(
            ReadingSession.duration_seconds > 60,
            ReadingSession.pages_turned > 0,
            ReadingSession.book_id.isnot(None),
        )
        .join(Book, Book.id == ReadingSession.book_id)
        .join(BookFile, BookFile.book_id == Book.id)
        .with_entities(
            BookFile.format.label("format"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.count(ReadingSession.id).label("sessions"),
        )
        .group_by(BookFile.format)
        .all()
    )
    pace_by_format = [
        {
            "format": r.format,
            "pages_per_min": round((r.pages / (r.seconds / 60)), 2) if r.seconds else 0,
            "sessions": int(r.sessions),
            "pages": int(r.pages),
            "seconds": int(r.seconds),
        }
        for r in pace_format_rows
    ]

    # ── Library growth timeline (last 24 months) ──────────────────────────────
    growth_cutoff = now - timedelta(days=365 * 2)
    growth_rows = (
        db.query(
            func.strftime('%Y-%m', Book.added_at).label("month"),
            func.coalesce(BookType.label, "Uncategorized").label("category"),
            func.count(Book.id).label("added"),
        )
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .filter(
            Book.status == "active",
            Book.added_at >= growth_cutoff,
            book_visibility_filter(db, current_user),
        )
        .group_by(func.strftime('%Y-%m', Book.added_at), func.coalesce(BookType.label, "Uncategorized"))
        .order_by("month")
        .all()
    )

    monthly_added: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_growth_cats: set[str] = set()
    for r in growth_rows:
        monthly_added[r.month][r.category] += int(r.added)
        all_growth_cats.add(r.category)

    library_growth: list[dict] = []
    running_total: dict[str, int] = {c: 0 for c in all_growth_cats}
    for i in range(23, -1, -1):
        d = now - timedelta(days=i * 30)
        month_key = d.strftime("%Y-%m")
        for cat, added in monthly_added.get(month_key, {}).items():
            running_total[cat] += added
        entry: dict = {"month": month_key, "total": sum(running_total.values())}
        for cat in sorted(all_growth_cats):
            entry[cat] = running_total[cat]
        library_growth.append(entry)

    # ── Ratings / taste (all-time, independent of the date window) ─────────────
    # Your taste isn't a 30-day thing, so these ignore cutoff/range entirely.
    book_seconds_all = rr.book_seconds(db, current_user.id, tz_modifier, covered, None, None)
    rated_rows = (
        db.query(
            Book.id, Book.title, Book.author, Book.cover_path,
            func.coalesce(BookType.label, "Uncategorized").label("category"),
            UserBookStatus.rating, UserBookStatus.rated_at, UserBookStatus.updated_at,
        )
        .select_from(UserBookStatus)
        .join(Book, Book.id == UserBookStatus.book_id)
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.rating.isnot(None),
            Book.status == "active",
            book_visibility_filter(db, current_user),
        )
        .all()
    )
    rated_books = sorted(
        [
            {
                "book_id": r.id, "title": r.title, "author": r.author,
                "has_cover": bool(r.cover_path), "category": r.category,
                "rating": int(r.rating),
                "seconds": int(book_seconds_all.get(r.id, (0, 0, 0))[0]),
                "rated_at": (r.rated_at or r.updated_at).isoformat() if (r.rated_at or r.updated_at) else None,
            }
            for r in rated_rows
        ],
        key=lambda b: b["rating"], reverse=True,
    )

    dist_counts = {i: 0 for i in range(1, 6)}
    cat_acc: dict[str, list[int]] = {}
    for b in rated_books:
        if 1 <= b["rating"] <= 5:
            dist_counts[b["rating"]] += 1
        cat_acc.setdefault(b["category"], []).append(b["rating"])
    rating_distribution = [{"rating": i, "count": dist_counts[i]} for i in range(1, 6)]
    rating_by_category = [
        {"category": c, "avg": round(sum(v) / len(v), 2), "count": len(v)}
        for c, v in sorted(cat_acc.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
    ]

    series_rating_rows = (
        db.query(UserSeriesRating.series_name, UserSeriesRating.rating)
        .filter(UserSeriesRating.user_id == current_user.id, UserSeriesRating.rating.isnot(None))
        .order_by(UserSeriesRating.rating.desc())
        .all()
    )
    series_sample: dict[str, int] = {}
    if series_rating_rows:
        names = [s.series_name for s in series_rating_rows]
        for sname, bid in (
            db.query(Book.series, func.min(Book.id))
            .filter(Book.series.in_(names), Book.status == "active")
            .group_by(Book.series)
        ):
            series_sample[sname] = bid
    series_ratings = [
        {"series": s.series_name, "rating": int(s.rating),
         "sample_book_id": series_sample.get(s.series_name)}
        for s in series_rating_rows
    ]

    rating_trend = sorted(
        [{"date": b["rated_at"][:10], "rating": b["rating"]} for b in rated_books if b["rated_at"]],
        key=lambda x: x["date"],
    )

    ratings = {
        "count": len(rated_books),
        "avg": round(sum(b["rating"] for b in rated_books) / len(rated_books), 2) if rated_books else 0,
        "distribution": rating_distribution,
        "by_category": rating_by_category,
        "books": rated_books,          # sorted rating desc; powers top/lowest + scatter
        "series": series_ratings,
        "trend": rating_trend,
    }

    # ── Lifetime / records / TBR / language (all-time, ignore the date range) ──
    lt_secs, lt_sessions, lt_pages = rr.totals(db, current_user.id, tz_modifier, covered, None, None)
    all_daily = rr.daily_map(db, current_user.id, tz_modifier, covered, None, None)
    lifetime = {
        "seconds": lt_secs,
        "sessions": lt_sessions,
        "pages": lt_pages,
        "books_finished": db.query(UserBookStatus).filter(
            UserBookStatus.user_id == current_user.id, UserBookStatus.status == "read").count(),
        "active_days": sum(1 for v in all_daily.values() if v[0] > 0),
        "longest_streak_days": longest_streak,
    }

    # Personal records
    longest_sess = (
        db.query(ReadingSession.duration_seconds, Book.title)
        .join(Book, Book.id == ReadingSession.book_id)
        .filter(ReadingSession.user_id == current_user.id, ReadingSession.duration_seconds.isnot(None))
        .order_by(ReadingSession.duration_seconds.desc())
        .first()
    )
    biggest_day = max(all_daily.items(), key=lambda kv: kv[1][0], default=None)   # by reading time
    pages_day = max(all_daily.items(), key=lambda kv: kv[1][2], default=None)      # by pages turned
    records = {
        "longest_session_seconds": int(longest_sess[0]) if longest_sess else 0,
        "longest_session_title": longest_sess[1] if longest_sess else None,
        "biggest_day_seconds": biggest_day[1][0] if biggest_day else 0,
        "biggest_day_date": biggest_day[0] if biggest_day else None,
        "most_pages_day": pages_day[1][2] if pages_day else 0,
        "most_pages_date": pages_day[0] if pages_day else None,
    }

    # TBR / library completion
    owned = db.query(Book).filter(
        Book.status == "active", book_visibility_filter(db, current_user)).count()
    status_counts = dict(
        db.query(UserBookStatus.status, func.count(UserBookStatus.id))
        .filter(UserBookStatus.user_id == current_user.id).group_by(UserBookStatus.status).all()
    )
    tbr_read = status_counts.get("read", 0)
    tbr_reading = status_counts.get("reading", 0)
    tbr_shelved = status_counts.get("shelved", 0)
    type_rows = (
        db.query(
            func.coalesce(BookType.label, "Uncategorized"),
            func.count(func.distinct(Book.id)),
            func.sum(case((UserBookStatus.status == "read", 1), else_=0)),
        )
        .select_from(Book)
        .outerjoin(BookType, BookType.id == Book.book_type_id)
        .outerjoin(UserBookStatus, (UserBookStatus.book_id == Book.id) & (UserBookStatus.user_id == current_user.id))
        .filter(Book.status == "active", book_visibility_filter(db, current_user))
        .group_by(func.coalesce(BookType.label, "Uncategorized"))
        .all()
    )
    tbr = {
        "owned": owned,
        "read": tbr_read,
        "reading": tbr_reading,
        "shelved": tbr_shelved,
        # books with no status row are implicitly unread
        "unread": max(owned - tbr_read - tbr_reading - tbr_shelved, 0),
        "pct": round(tbr_read / owned * 100, 1) if owned else 0,
        "by_type": sorted(
            [
                {"type": label, "owned": int(o), "read": int(rd or 0),
                 "pct": round(int(rd or 0) / int(o) * 100, 1) if o else 0}
                for label, o, rd in type_rows if o
            ],
            key=lambda x: x["owned"], reverse=True,
        ),
    }

    # Reading time by language (normalized: en/eng/English -> one entry)
    from backend.services.languages import normalize_language, language_label
    lang_acc: dict[str, list] = {}
    if book_seconds_all:
        for bid, lang in db.query(Book.id, Book.language).filter(Book.id.in_(list(book_seconds_all.keys()))):
            code = normalize_language(lang) or ""
            e = lang_acc.setdefault(code, [0, 0])
            e[0] += int(book_seconds_all.get(bid, (0, 0, 0))[0])
            e[1] += 1
    language = sorted(
        [
            {"language": language_label(c) if c else "Unknown", "code": c or "unknown",
             "seconds": v[0], "books": v[1]}
            for c, v in lang_acc.items()
        ],
        key=lambda x: x["seconds"], reverse=True,
    )

    # ── Word-count powered insights (Phase 4, all-time) ────────────────────────
    # Words read (lifetime + by finish-year), true WPM (words ÷ reconciled
    # read-time), and book-length distribution/trend. All derive from one pass
    # over the user's finished ("read") books that have a parsed word_count.
    import statistics as _statistics

    read_word_rows = (
        db.query(
            Book.id, Book.title, Book.author, Book.cover_path, Book.word_count,
            UserBookStatus.updated_at,
        )
        .select_from(UserBookStatus)
        .join(Book, Book.id == UserBookStatus.book_id)
        .filter(
            UserBookStatus.user_id == current_user.id,
            UserBookStatus.status == "read",
            Book.status == "active",
            Book.word_count.isnot(None),
            book_visibility_filter(db, current_user),
        )
        .all()
    )

    WPM_MIN_SECONDS = 300  # below ~5 min of read-time a pace number is just noise

    total_words = 0
    words_by_year: dict[int, int] = {}
    length_books: list[dict] = []
    wpm_books: list[dict] = []
    for r in read_word_rows:
        wc = int(r.word_count)
        total_words += wc
        finished = r.updated_at
        if finished:
            words_by_year[finished.year] = words_by_year.get(finished.year, 0) + wc
        length_books.append({
            "book_id": r.id, "title": r.title, "has_cover": bool(r.cover_path),
            "words": wc,
            "finished": finished.date().isoformat() if finished else None,
        })
        secs = int(book_seconds_all.get(r.id, (0, 0, 0))[0])
        if secs >= WPM_MIN_SECONDS:
            wpm_books.append({
                "book_id": r.id, "title": r.title, "author": r.author,
                "has_cover": bool(r.cover_path),
                "words": wc, "seconds": secs,
                "wpm": round(wc * 60 / secs, 1),
            })

    words = {
        "total_words": total_words,
        "books_counted": len(length_books),
        "by_year": [{"year": y, "words": w} for y, w in sorted(words_by_year.items())],
    }

    wpm_secs = sum(b["seconds"] for b in wpm_books)
    wpm_words = sum(b["words"] for b in wpm_books)
    wpm = {
        "overall": round(wpm_words * 60 / wpm_secs, 1) if wpm_secs else 0,
        "books_counted": len(wpm_books),
        "books": sorted(wpm_books, key=lambda b: b["wpm"], reverse=True),
    }

    length_vals = [b["words"] for b in length_books]
    _bucket_defs = [
        (0, 50_000, "<50k"), (50_000, 100_000, "50–100k"),
        (100_000, 150_000, "100–150k"), (150_000, 250_000, "150–250k"),
        (250_000, None, "250k+"),
    ]
    length_buckets = [
        {"label": lbl, "count": sum(1 for v in length_vals if v >= lo and (hi is None or v < hi))}
        for lo, hi, lbl in _bucket_defs
    ]
    _len_year: dict[int, list[int]] = {}
    for b in length_books:
        if b["finished"]:
            _len_year.setdefault(int(b["finished"][:4]), []).append(b["words"])
    book_lengths = {
        "count": len(length_vals),
        "avg_words": int(_statistics.mean(length_vals)) if length_vals else 0,
        "median_words": int(_statistics.median(length_vals)) if length_vals else 0,
        "longest": max(length_books, key=lambda b: b["words"]) if length_books else None,
        "buckets": length_buckets,
        "by_year": [
            {"year": y, "avg_words": int(_statistics.mean(v)), "count": len(v)}
            for y, v in sorted(_len_year.items())
        ],
        "books": length_books,
    }

    # ── Re-reads (all-time) — books with pages revisited on a later day ─────────
    # A page read on 2+ distinct local-days is a re-read. We rank books by how
    # many of their pages got revisited. Pure page-stats, no plugin work.
    from backend.models.ko_stats import PageStat as _PageStat
    _day = func.cast(_PageStat.start_time / 86400, Integer)
    _reread_pages_sq = (
        db.query(
            _PageStat.book_id.label("book_id"),
            _PageStat.page.label("page"),
        )
        .filter(_PageStat.user_id == current_user.id)
        .group_by(_PageStat.book_id, _PageStat.page)
        .having(func.count(func.distinct(_day)) > 1)
        .subquery()
    )
    reread_counts = dict(
        db.query(_reread_pages_sq.c.book_id, func.count())
        .group_by(_reread_pages_sq.c.book_id)
        .all()
    )
    reread_books: list[dict] = []
    if reread_counts:
        rr_ids = list(reread_counts.keys())
        rr_totals = dict(
            db.query(_PageStat.book_id, func.max(_PageStat.total_pages))
            .filter(_PageStat.user_id == current_user.id, _PageStat.book_id.in_(rr_ids))
            .group_by(_PageStat.book_id).all()
        )
        rr_meta = {
            b.id: b for b in db.query(Book).filter(
                Book.id.in_(rr_ids), Book.status == "active",
                book_visibility_filter(db, current_user),
            )
        }
        for bid, n in reread_counts.items():
            bk = rr_meta.get(bid)
            if not bk or int(n) < 3:          # ignore 1–2 stray revisited pages
                continue
            total = int(rr_totals.get(bid) or 0)
            reread_books.append({
                "book_id": bid, "title": bk.title, "author": bk.author,
                "has_cover": bool(bk.cover_path),
                "reread_pages": int(n), "total_pages": total,
                "pct": round(int(n) / total * 100, 1) if total else 0,
            })
        reread_books.sort(key=lambda b: b["reread_pages"], reverse=True)
    rereads = {"books": reread_books}

    return {
        "range_days": effective_days,
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
        "reading_pace": reading_pace,
        "books_in_progress": books_in_progress,
        "session_timeline": session_timeline,
        "year_summary": year_summary,
        "period_comparison": period_comparison,
        "per_book_time": per_book_time,
        "monthly_comparison": monthly_comparison,
        "genre_over_time": genre_over_time,
        "hour_dow_heatmap": hour_dow_heatmap,
        "series_completion": series_completion,
        "author_affinity": author_affinity,
        "completion_rate": completion_rate,
        "completion_by_type": completion_by_type,
        "pace_by_format": pace_by_format,
        "library_growth": library_growth,
        "ratings": ratings,
        "lifetime": lifetime,
        "records": records,
        "tbr": tbr,
        "language": language,
        "words": words,
        "wpm": wpm,
        "book_lengths": book_lengths,
        "rereads": rereads,
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

    from backend.models.ko_stats import PageStat
    window_epoch = int(window_start.replace(tzinfo=timezone.utc).timestamp())

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

        # Honest progress + pace from imported KOReader page-stats, when present:
        # distinct pages read ÷ real total_pages, and a page-stats reading-day
        # count so device-only books (no live sessions) still get an estimate.
        ps = (
            db.query(
                func.count(func.distinct(PageStat.page)),
                func.max(PageStat.total_pages),
                func.count(func.distinct(case((PageStat.start_time >= window_epoch, PageStat.page)))),
                func.count(func.distinct(case((PageStat.start_time >= window_epoch,
                                               func.cast(PageStat.start_time / 86400, Integer))))),
                func.max(PageStat.page),
            )
            .filter(PageStat.user_id == current_user.id, PageStat.book_id == row.id)
            .one()
        )
        ps_pages, ps_total, ps_pages_30, ps_days_30, ps_max_page = (
            int(ps[0] or 0), int(ps[1] or 0), int(ps[2] or 0), int(ps[3] or 0), int(ps[4] or 0))
        ps_days_30 = max(ps_days_30 - (1 if ps_pages_30 else 0), 0)  # gained over the gaps, not the first day
        # Progress = how far through: the best of the synced position (set above)
        # and the furthest page reached. Page-stats are coverage, so use the
        # furthest page, not the distinct-page count. max() covers a synced
        # position ahead of the dwell history *and* a stale position behind it.
        page_pct = round(ps_max_page / ps_total * 100, 1) if (ps_total > 0 and ps_max_page > 0) else 0.0
        progress = min(max(progress, page_pct), 100.0)

        estimated_days: Optional[int] = None
        if session_count == 0 and ps_total > 0 and ps_pages_30 > 0 and ps_days_30 >= 1 and progress < 100:
            # Device-only: pages read per active-day → days to read the rest.
            pages_per_day = ps_pages_30 / ps_days_30
            # Remaining measured from your position, not from uncovered pages.
            remaining_pages = max(round((1 - progress / 100.0) * ps_total), 0)
            estimated_days = max(1, round(remaining_pages / pages_per_day)) if pages_per_day else None
        elif session_count > 0 and progress >= 5 and progress < 100:
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

        # Confidence from whichever signal drove the estimate (live sessions or
        # page-stat reading-days for device-only books).
        evidence = max(session_count, ps_days_30)
        if evidence >= 5:
            confidence = "high"
        elif evidence >= 2:
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


# ── Dashboard persistence ────────────────────────────────────────────────────
# The customisable stats dashboard (boards/tiles/layouts) is stored per user as
# an opaque JSON blob — the frontend owns the shape, so catalog changes never
# need a backend change. Last write wins across devices.

_DASHBOARD_MAX_BYTES = 256 * 1024


class DashboardPayload(BaseModel):
    data: dict


@router.get("/stats/dashboard")
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(UserDashboard).filter(UserDashboard.user_id == current_user.id).first()
    return {"data": json.loads(row.data) if row else None}


@router.put("/stats/dashboard")
def put_dashboard(
    payload: DashboardPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw = json.dumps(payload.data, separators=(",", ":"))
    if len(raw.encode()) > _DASHBOARD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Dashboard too large")
    row = db.query(UserDashboard).filter(UserDashboard.user_id == current_user.id).first()
    if row:
        row.data = raw
    else:
        db.add(UserDashboard(user_id=current_user.id, data=raw))
    db.commit()
    return {"ok": True}
