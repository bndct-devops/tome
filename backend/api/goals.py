"""Reading goals API.

Router has no prefix and is included under /api (backend/main.py), so every
path here is /api/goals...

  GET    /goals                    all goals with their current period (cards)
  POST   /goals                    create
  PUT    /goals/{id}               change the target (count goals) or due date (finish-by)
  DELETE /goals/{id}               soft delete, restorable for RESTORE_WINDOW
  POST   /goals/{id}/restore       undo a delete
  GET    /goals/{id}/history       past periods, met / missed, figures
  GET    /goals/kinds              the dialog's kinds, with the ones the reader has
  GET    /goals/suggestions        suggested targets from the reader's own reading
  GET    /goals/finish-preview     what finishing a book or series by a day takes

Every endpoint that computes takes the client's zone like /api/stats: ``tz``
(IANA name, DST-correct) and ``tz_offset`` (JS getTimezoneOffset, for older
clients; used alone when ``tz`` is missing or unknown). All computation lives in
backend/services/goals.py.

goal_reached notifications are still created lazily when goals are read.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.library import BookType
from backend.models.reading_goal import ReadingGoal, ReadingGoalTarget, live_goals
from backend.models.user import User
from backend.services import goals as gs
from backend.services.reading_day import DayCtx

router = APIRouter(tags=["goals"])

# Kept for importers of the first goals build.
ALLOWED_KINDS: set[str] = set(gs.KINDS)


# ── Schemas ───────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    kind: str
    target: Optional[int] = None
    book_type_id: Optional[int] = None
    # finish_by: exactly one of series / book_id, and the day (inclusive).
    series: Optional[str] = None
    book_id: Optional[int] = None
    due_date: Optional[date] = None


class GoalUpdate(BaseModel):
    target: Optional[int] = None
    due_date: Optional[date] = None


def _err(msg: str):
    raise HTTPException(status_code=422, detail=msg)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ctx(tz_offset: int, tz: Optional[str]) -> DayCtx:
    return DayCtx(tz_offset, tz)


def _data(db: Session, user: User, tz_offset: int, tz: Optional[str]) -> gs.GoalData:
    return gs.GoalData(db, user, _ctx(tz_offset, tz), gs.utcnow())


def _purge_deleted(db: Session, user_id: int) -> None:
    cutoff = gs.utcnow() - gs.RESTORE_WINDOW
    (db.query(ReadingGoal)
       .filter(ReadingGoal.user_id == user_id, ReadingGoal.deleted_at.isnot(None),
               ReadingGoal.deleted_at < cutoff)
       .delete(synchronize_session=False))


def _user_goals(db: Session, user_id: int) -> list[ReadingGoal]:
    return (
        live_goals(db.query(ReadingGoal))
        .filter(ReadingGoal.user_id == user_id)
        .order_by(ReadingGoal.created_at, ReadingGoal.id)
        .all()
    )


def _own_goal(db: Session, user: User, goal_id: int, deleted: bool = False) -> ReadingGoal:
    q = db.query(ReadingGoal).filter(ReadingGoal.id == goal_id, ReadingGoal.user_id == user.id)
    q = q.filter(ReadingGoal.deleted_at.isnot(None)) if deleted else live_goals(q)
    goal = q.first()
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


def _duplicate(db: Session, user_id: int, kind: str, book_type_id: Optional[int],
               series: Optional[str], book_id: Optional[int], exclude: Optional[int] = None):
    q = live_goals(db.query(ReadingGoal)).filter(ReadingGoal.user_id == user_id, ReadingGoal.kind == kind)
    if kind == "finish_by":
        q = q.filter(ReadingGoal.target_series == series) if series is not None else \
            q.filter(ReadingGoal.target_series.is_(None), ReadingGoal.target_book_id == book_id)
    elif book_type_id is None:
        q = q.filter(ReadingGoal.book_type_id.is_(None))
    else:
        q = q.filter(ReadingGoal.book_type_id == book_type_id)
    if exclude is not None:
        q = q.filter(ReadingGoal.id != exclude)
    return q.first()


def _state(gd: gs.GoalData, goal: ReadingGoal) -> dict:
    state = gs.goal_state(gd, goal)
    gs.maybe_notify(gd, goal, state)
    return state


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/goals")
def list_goals(
    tz_offset: int = Query(0, description="JS getTimezoneOffset() in minutes"),
    tz: Optional[str] = Query(None, description="IANA timezone name (DST-correct reading days)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """All live goals with their current period. Also creates goal_reached
    notifications (once per period) for goals reached now."""
    _purge_deleted(db, current_user.id)
    gd = _data(db, current_user, tz_offset, tz)
    payload = [_state(gd, g) for g in _user_goals(db, current_user.id)]
    db.commit()
    return {"goals": payload, "week_start": gd.week_start, "today": gd.today.isoformat()}


@router.get("/goals/kinds")
def list_kinds(
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """What the create dialog offers: every kind with its name, unit, presets,
    whether it can be limited to a book type, whether it is new, and the
    reader's goals of that kind (by id; their progress is in GET /goals)."""
    gd = _data(db, current_user, tz_offset, tz)
    goals = _user_goals(db, current_user.id)
    out = []
    for kind, (metric, period) in gs.KINDS.items():
        start = gs.period_bounds(period, gd.today, gd.week_start)[0] if period != "deadline" else None
        names = gs.goal_name(kind, start, None, gd.today, "", gd.today) if kind != "finish_by" else \
            {"name": "Finish by a date", "name_key": "finish_by", "name_params": {}}
        out.append({
            "kind": kind, "metric": metric, "period": period, "unit": gs.UNITS[metric],
            **names,
            "presets": gs.PRESETS.get(kind, []),
            "min": 1, "max": 7 if kind == "days_per_week" else None,
            "book_type_scoped": kind in gs.TYPE_SCOPED_KINDS,
            "new": kind in gs.NEW_KINDS,
            "existing": [
                {"goal_id": g.id, "book_type_id": g.book_type_id,
                 "target_series": g.target_series, "target_book_id": g.target_book_id}
                for g in goals if g.kind == kind
            ],
        })
    return {"kinds": out, "week_start": gd.week_start}


@router.get("/goals/suggestions")
def get_suggestions(
    kind: Optional[str] = Query(None, description="One kind; all count kinds when omitted"),
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if kind is not None and (kind not in gs.KINDS or kind == "finish_by"):
        _err(f"No suggestions for kind {kind!r}")
    gd = _data(db, current_user, tz_offset, tz)
    return gs.suggestions(gd, [kind] if kind else None)


@router.get("/goals/finish-preview")
def finish_preview(
    due: date = Query(..., description="The day to finish by (YYYY-MM-DD, local, inclusive)"),
    series: Optional[str] = Query(None),
    book_id: Optional[int] = Query(None),
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The finish-by figures for a book or series before a goal is set."""
    if (series is None) == (book_id is None):
        _err("Give either series or book_id")
    gd = _data(db, current_user, tz_offset, tz)
    est = gs.finish_estimate(gd, series, book_id, due)
    if not est["volumes_total"]:
        raise HTTPException(status_code=404, detail="Nothing to finish there")
    return est


@router.post("/goals", status_code=201)
def create_goal(
    body: GoalCreate,
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a goal. 409 if the same goal already exists (same kind and book
    type; finish-by: same series or book)."""
    if body.kind not in gs.KINDS:
        _err(f"Invalid goal kind {body.kind!r}. Allowed: {sorted(gs.KINDS)}")
    gd = _data(db, current_user, tz_offset, tz)
    if body.book_type_id is not None:
        if body.kind not in gs.TYPE_SCOPED_KINDS:
            _err("This kind of goal cannot be limited to a book type")
        if db.get(BookType, body.book_type_id) is None:
            _err("Unknown book type")

    series = book_id = due = None
    if body.kind == "finish_by":
        if (body.series is None) == (body.book_id is None):
            _err("A finish-by goal needs either series or book_id")
        if body.due_date is None:
            _err("A finish-by goal needs due_date")
        if body.due_date < gd.today:
            _err("due_date is in the past")
        series, book_id, due = body.series, body.book_id, body.due_date
        if book_id is not None and db.get(Book, book_id) is None:
            _err("Unknown book")
        est = gs.finish_estimate(gd, series, book_id, due)
        if not est["volumes_total"]:
            _err("Nothing to finish there")
        target = est["volumes_total"]
    else:
        if body.target is None or body.target <= 0:
            _err("target must be greater than 0")
        if body.kind == "days_per_week" and body.target > 7:
            _err("A week has 7 days")
        target = body.target

    if _duplicate(db, current_user.id, body.kind, body.book_type_id, series, book_id):
        raise HTTPException(status_code=409, detail="This goal already exists — edit it instead")
    # A deleted twin waiting for undo gives way to the new goal.
    for twin in db.query(ReadingGoal).filter(
        ReadingGoal.user_id == current_user.id, ReadingGoal.kind == body.kind,
        ReadingGoal.deleted_at.isnot(None),
    ).all():
        same = (twin.target_series == series and (series is not None or twin.target_book_id == book_id)) \
            if body.kind == "finish_by" else twin.book_type_id == body.book_type_id
        if same:
            db.delete(twin)

    goal = ReadingGoal(
        user_id=current_user.id,
        kind=body.kind,
        target=target,
        book_type_id=body.book_type_id,
        start_date=gd.today,
        target_series=series,
        target_book_id=book_id if series is None else None,
        due_date=due,
    )
    db.add(goal)
    db.flush()
    db.add(ReadingGoalTarget(goal_id=goal.id, target=target, changed_on=gd.today))
    db.flush()
    payload = _state(gd, goal)
    db.commit()
    return payload


@router.put("/goals/{goal_id}")
def update_goal(
    goal_id: int,
    body: GoalUpdate,
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Change a count goal's target (it applies to the current period; past
    periods keep theirs) or a finish-by goal's due date. A changed target or
    date clears the notified marker so the goal can notify again."""
    goal = _own_goal(db, current_user, goal_id)
    gd = _data(db, current_user, tz_offset, tz)
    if goal.kind == "finish_by":
        if body.due_date is None:
            _err("A finish-by goal changes by due_date")
        if body.due_date != goal.due_date:
            if body.due_date < gd.today:
                _err("due_date is in the past")
            goal.due_date = body.due_date
            goal.updated_at = datetime.utcnow()
    else:
        if body.target is None or body.target <= 0:
            _err("target must be greater than 0")
        if goal.kind == "days_per_week" and body.target > 7:
            _err("A week has 7 days")
        if body.target != goal.target:
            goal.target = body.target
            goal.notified_window_start = None
            goal.updated_at = datetime.utcnow()
            db.add(ReadingGoalTarget(goal_id=goal.id, target=body.target, changed_on=gd.today))
            db.flush()
    payload = _state(gd, goal)
    db.commit()
    return payload


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(
    goal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a goal. 404 if it doesn't exist or belongs to someone else.
    It stays restorable (POST /goals/{id}/restore) for goals.RESTORE_WINDOW."""
    goal = _own_goal(db, current_user, goal_id)
    goal.deleted_at = gs.utcnow()
    db.commit()


@router.post("/goals/{goal_id}/restore")
def restore_goal(
    goal_id: int,
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Undo a delete: the goal comes back with its history. 404 once purged,
    409 when an equal goal was created in the meantime."""
    _purge_deleted(db, current_user.id)
    goal = _own_goal(db, current_user, goal_id, deleted=True)
    if _duplicate(db, current_user.id, goal.kind, goal.book_type_id, goal.target_series,
                  goal.target_book_id, exclude=goal.id):
        raise HTTPException(status_code=409, detail="This goal exists again")
    goal.deleted_at = None
    gd = _data(db, current_user, tz_offset, tz)
    payload = _state(gd, goal)
    db.commit()
    return payload


@router.get("/goals/{goal_id}/history")
def goal_history(
    goal_id: int,
    periods: Optional[int] = Query(None, ge=1, le=120,
                                   description="Past periods (day goals: weeks); default 12, years 5"),
    tz_offset: int = Query(0),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    goal = _own_goal(db, current_user, goal_id)
    gd = _data(db, current_user, tz_offset, tz)
    out = gs.goal_history(gd, goal, periods)
    gs.maybe_notify(gd, goal, out["state"])
    db.commit()
    return out
