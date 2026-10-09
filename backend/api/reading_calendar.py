"""Reading Calendar endpoints: a month grid and one day in detail.

Static paths under /stats — registered in their own router so they can't be
shadowed by anything in stats.py.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services import reading_calendar
from backend.services.reading_day import DayCtx

router = APIRouter(tags=["stats"])


@router.get("/stats/calendar")
def get_calendar_month(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
    tz: str | None = Query(None, description="IANA timezone name for DST-correct day bucketing"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    year, mon = int(month[:4]), int(month[5:])
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    return reading_calendar.month_view(db, current_user.id, DayCtx(tz_offset, tz), year, mon)


@router.get("/stats/calendar/day")
def get_calendar_day(
    day: str = Query(..., description="YYYY-MM-DD (a reading day)"),
    tz_offset: int = Query(0, description="Client timezone offset in minutes (JS getTimezoneOffset)"),
    tz: str | None = Query(None, description="IANA timezone name for DST-correct day bucketing"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    return reading_calendar.day_view(db, current_user.id, DayCtx(tz_offset, tz), d)
