"""Reading goals API — generalized metric × period model.

Supported kinds (ALLOWED_KINDS):
  books_per_year, books_per_month,
  minutes_per_day, minutes_per_week,
  pages_per_day, pages_per_week

Each kind is parsed as {metric}_per_{period}:
  metric ∈ books | minutes | pages
  period ∈ day | week | month | year

A goal may be scoped to a book type (book_type_id) so "20 books this year"
and "20 manga this year" coexist as separate goals. Uniqueness per
(user, kind, book_type_id) is enforced here, not in the DB (NULL semantics).

Window semantics: day/month/year are local calendar windows; week is the
last 7 local days inclusive of today (matches the stats heatmap/streak
windows). goal_reached notifications are created lazily when progress is
read, and only for month/year goals — day/week windows roll too often and
would flood the bell.
"""
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.library import BookType
from backend.models.notification import Notification
from backend.models.reading_goal import ReadingGoal
from backend.models.tome_sync import ReadingSession
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus

router = APIRouter(tags=["goals"])

ALLOWED_KINDS: set[str] = {
    "books_per_year",
    "books_per_month",
    "minutes_per_day",
    "minutes_per_week",
    "pages_per_day",
    "pages_per_week",
}

Metric = Literal["books", "minutes", "pages"]
Period = Literal["day", "week", "month", "year"]


# ── Schemas ───────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    kind: str
    target: int
    book_type_id: Optional[int] = None

    @field_validator("target")
    @classmethod
    def target_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("target must be greater than 0")
        return v

    @field_validator("kind")
    @classmethod
    def kind_allowed(cls, v: str) -> str:
        if v not in ALLOWED_KINDS:
            raise ValueError(f"Invalid goal kind {v!r}. Allowed: {sorted(ALLOWED_KINDS)}")
        return v


class GoalUpdate(BaseModel):
    target: int

    @field_validator("target")
    @classmethod
    def target_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("target must be greater than 0")
        return v


# ── Kind parser ───────────────────────────────────────────────────────────────

def _parse_kind(kind: str) -> tuple[Metric, Period]:
    """Parse 'metric_per_period' into (metric, period). Raises ValueError on malformed kind."""
    parts = kind.split("_per_", 1)
    if len(parts) != 2:
        raise ValueError(f"Cannot parse goal kind: {kind!r}")
    metric, period = parts[0], parts[1]
    if metric not in ("books", "minutes", "pages"):
        raise ValueError(f"Unknown metric: {metric!r}")
    if period not in ("day", "week", "month", "year"):
        raise ValueError(f"Unknown period: {period!r}")
    return metric, period  # type: ignore[return-value]


# ── Window helpers ────────────────────────────────────────────────────────────

def _local_now(tz_offset: int) -> datetime:
    """Current local time. tz_offset is JS getTimezoneOffset (positive = west of UTC)."""
    return datetime.utcnow() + timedelta(minutes=-tz_offset)


def _tz_modifier(tz_offset: int) -> str:
    """Convert JS getTimezoneOffset (positive=west) to SQLite datetime modifier."""
    offset_hours = -(tz_offset // 60)
    return f"{offset_hours:+d} hours"


def _window_bounds(period: Period, tz_offset: int) -> tuple[datetime, datetime]:
    """Return (start, end) of the current local period window, both as UTC datetimes."""
    local_offset = timedelta(minutes=-tz_offset)
    now_local = _local_now(tz_offset)
    today_local = datetime(now_local.year, now_local.month, now_local.day)

    if period == "day":
        start_local = today_local
        end_local = start_local + timedelta(days=1)
    elif period == "week":
        # last 7 local days inclusive of today — match existing days_hit_this_week window
        start_local = today_local - timedelta(days=6)
        end_local = today_local + timedelta(days=1)
    elif period == "month":
        start_local = datetime(now_local.year, now_local.month, 1)
        end_local = (
            datetime(now_local.year + 1, 1, 1)
            if now_local.month == 12
            else datetime(now_local.year, now_local.month + 1, 1)
        )
    else:  # year
        start_local = datetime(now_local.year, 1, 1)
        end_local = datetime(now_local.year + 1, 1, 1)

    return start_local - local_offset, end_local - local_offset


def _window_start(period: Period, tz_offset: int) -> datetime:
    return _window_bounds(period, tz_offset)[0]


def _expected_now(period: Period, target: int, tz_offset: int) -> Optional[float]:
    """Target prorated to the elapsed fraction of the window — the "on pace" line.

    Only meaningful for calendar windows that fill up over time (month/year);
    day is too short to pace and week is a rolling window (always "full").
    """
    if period not in ("month", "year"):
        return None
    start, end = _window_bounds(period, tz_offset)
    elapsed = (datetime.utcnow() - start).total_seconds()
    total = (end - start).total_seconds()
    return round(target * max(0.0, min(elapsed / total, 1.0)), 1)


# ── Progress helpers ──────────────────────────────────────────────────────────

def _compute_books_current(
    db: Session, user_id: int, period: Period, tz_offset: int, book_type_id: Optional[int]
) -> int:
    """Count UserBookStatus rows with status='read' and updated_at in the current period window."""
    start = _window_start(period, tz_offset)
    q = db.query(UserBookStatus).filter(
        UserBookStatus.user_id == user_id,
        UserBookStatus.status == "read",
        UserBookStatus.updated_at >= start,
    )
    if book_type_id is not None:
        q = q.join(Book, Book.id == UserBookStatus.book_id).filter(
            Book.book_type_id == book_type_id
        )
    return q.count()


def _session_sum(
    db: Session,
    user_id: int,
    column,
    period: Period,
    tz_offset: int,
    book_type_id: Optional[int],
) -> int:
    """Sum a ReadingSession column over the current period window."""
    start = _window_start(period, tz_offset)
    q = db.query(func.coalesce(func.sum(column), 0)).filter(
        ReadingSession.user_id == user_id,
        ReadingSession.started_at >= start,
    )
    if book_type_id is not None:
        q = q.join(Book, Book.id == ReadingSession.book_id).filter(
            Book.book_type_id == book_type_id
        )
    return int(q.scalar() or 0)


def _compute_current(
    db: Session, user_id: int, metric: Metric, period: Period, tz_offset: int,
    book_type_id: Optional[int],
) -> float:
    if metric == "books":
        return _compute_books_current(db, user_id, period, tz_offset, book_type_id)
    if metric == "minutes":
        seconds = _session_sum(
            db, user_id, ReadingSession.duration_seconds, period, tz_offset, book_type_id
        )
        return round(seconds / 60, 1)
    return _session_sum(
        db, user_id, ReadingSession.pages_turned, period, tz_offset, book_type_id
    )


def _compute_days_hit(
    db: Session,
    user_id: int,
    metric: Metric,
    target: int,
    tz_offset: int,
    book_type_id: Optional[int],
) -> int:
    """
    For daily goals: count days in the last 7 local days (including today)
    where the user's total for the metric >= target.
    """
    modifier = _tz_modifier(tz_offset)
    week_cutoff = datetime.utcnow() - timedelta(days=7)

    if metric == "books":
        q = db.query(
            func.date(UserBookStatus.updated_at, modifier).label("day"),
            func.count(UserBookStatus.id).label("total"),
        ).filter(
            UserBookStatus.user_id == user_id,
            UserBookStatus.status == "read",
            UserBookStatus.updated_at >= week_cutoff,
        )
        if book_type_id is not None:
            q = q.join(Book, Book.id == UserBookStatus.book_id).filter(
                Book.book_type_id == book_type_id
            )
        daily_rows = q.group_by(func.date(UserBookStatus.updated_at, modifier)).all()
        daily_map = {row.day: int(row.total) for row in daily_rows}
    else:
        column = (
            ReadingSession.duration_seconds
            if metric == "minutes"
            else ReadingSession.pages_turned
        )
        q = db.query(
            func.date(ReadingSession.started_at, modifier).label("day"),
            func.coalesce(func.sum(column), 0).label("total"),
        ).filter(
            ReadingSession.user_id == user_id,
            ReadingSession.started_at >= week_cutoff,
        )
        if book_type_id is not None:
            q = q.join(Book, Book.id == ReadingSession.book_id).filter(
                Book.book_type_id == book_type_id
            )
        daily_rows = q.group_by(func.date(ReadingSession.started_at, modifier)).all()
        if metric == "minutes":
            daily_map = {row.day: int(row.total) // 60 for row in daily_rows}
        else:
            daily_map = {row.day: int(row.total) for row in daily_rows}

    return sum(1 for v in daily_map.values() if v >= target)


# ── Notification ──────────────────────────────────────────────────────────────

def _goal_phrase(metric: Metric, period: Period, target: int, type_label: Optional[str], tz_offset: int) -> str:
    """Human phrase for a goal, e.g. '20 books in 2026' or '300 minutes this week (Manga)'."""
    noun = {"books": "books" if target != 1 else "book", "minutes": "minutes", "pages": "pages"}[metric]
    when = {
        "day": "today",
        "week": "this week",
        "month": "this month",
        "year": f"in {_local_now(tz_offset).year}",
    }[period]
    phrase = f"{target} {noun} {when}"
    if type_label:
        phrase += f" ({type_label})"
    return phrase


def _maybe_notify_reached(
    db: Session,
    goal: ReadingGoal,
    metric: Metric,
    period: Period,
    current: float,
    type_label: Optional[str],
    tz_offset: int,
) -> bool:
    """Create a goal_reached notification once per month/year window. Returns True if created."""
    if period not in ("month", "year") or current < goal.target:
        return False
    window_start = _window_start(period, tz_offset)
    if goal.notified_window_start == window_start:
        return False
    db.add(
        Notification(
            user_id=goal.user_id,
            kind="goal_reached",
            title="Reading goal reached",
            body=f"You hit your goal of {_goal_phrase(metric, period, goal.target, type_label, tz_offset)}.",
            link="/stats",
        )
    )
    goal.notified_window_start = window_start
    return True


# ── Response builder ──────────────────────────────────────────────────────────

def _build_goal_response(db: Session, goal: ReadingGoal, tz_offset: int) -> dict:
    metric, period = _parse_kind(goal.kind)

    type_label: Optional[str] = None
    if goal.book_type_id is not None:
        bt = db.query(BookType).filter(BookType.id == goal.book_type_id).first()
        type_label = bt.label if bt else None

    current = _compute_current(db, goal.user_id, metric, period, tz_offset, goal.book_type_id)
    pct = round(current / goal.target * 100, 1) if goal.target > 0 else 0.0

    result: dict = {
        "id": goal.id,
        "kind": goal.kind,
        "metric": metric,
        "period": period,
        "target": goal.target,
        "book_type_id": goal.book_type_id,
        "book_type_label": type_label,
        "current": current,
        "pct": pct,
        "expected": _expected_now(period, goal.target, tz_offset),
    }

    if period == "day":
        result["days_hit_this_week"] = _compute_days_hit(
            db, goal.user_id, metric, goal.target, tz_offset, goal.book_type_id
        )
    if period == "year":
        result["year"] = _local_now(tz_offset).year

    _maybe_notify_reached(db, goal, metric, period, current, type_label, tz_offset)
    return result


def _user_goals(db: Session, user_id: int) -> list[ReadingGoal]:
    return (
        db.query(ReadingGoal)
        .filter(ReadingGoal.user_id == user_id)
        .order_by(ReadingGoal.created_at, ReadingGoal.id)
        .all()
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/goals")
def list_goals(
    tz_offset: int = Query(0, description="JS getTimezoneOffset() in minutes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return all goals for the current user with computed progress.

    Also lazily creates goal_reached notifications — this endpoint is hit from
    the Home tab and the stats dashboard, so it's the natural evaluation point.
    """
    goals = _user_goals(db, current_user.id)
    payload = [_build_goal_response(db, g, tz_offset) for g in goals]
    db.commit()  # persist any notifications / notified_window_start updates
    return {"goals": payload}


@router.post("/goals", status_code=201)
def create_goal(
    body: GoalCreate,
    tz_offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a goal. 409 if the same (kind, book type) goal already exists."""
    if body.book_type_id is not None:
        bt = db.query(BookType).filter(BookType.id == body.book_type_id).first()
        if bt is None:
            raise HTTPException(status_code=422, detail="Unknown book type")

    existing = (
        db.query(ReadingGoal)
        .filter(
            ReadingGoal.user_id == current_user.id,
            ReadingGoal.kind == body.kind,
            ReadingGoal.book_type_id == body.book_type_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="This goal already exists — edit it instead")

    goal = ReadingGoal(
        user_id=current_user.id,
        kind=body.kind,
        target=body.target,
        book_type_id=body.book_type_id,
    )
    db.add(goal)
    db.flush()
    payload = _build_goal_response(db, goal, tz_offset)
    db.commit()
    return payload


@router.put("/goals/{goal_id}")
def update_goal(
    goal_id: int,
    body: GoalUpdate,
    tz_offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update a goal's target. Clears the notified marker so a raised target can re-notify."""
    goal = (
        db.query(ReadingGoal)
        .filter(ReadingGoal.id == goal_id, ReadingGoal.user_id == current_user.id)
        .first()
    )
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    if body.target != goal.target:
        goal.target = body.target
        goal.notified_window_start = None
        goal.updated_at = datetime.utcnow()

    payload = _build_goal_response(db, goal, tz_offset)
    db.commit()
    return payload


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(
    goal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Remove a goal. 404 if it doesn't exist or belongs to someone else."""
    goal = (
        db.query(ReadingGoal)
        .filter(ReadingGoal.id == goal_id, ReadingGoal.user_id == current_user.id)
        .first()
    )
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    db.delete(goal)
    db.commit()
