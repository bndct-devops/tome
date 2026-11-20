"""ReadingGoal model — per-user reading goals.

Kinds (curated list in backend/services/goals.py:KINDS):
  {metric}_per_{period}  metric ∈ books | minutes | pages, period ∈ day | week | month | year
  days_per_week          read on N days a week (a reading day as the streak counts it)
  finish_by              finish a book or a series by ``due_date``

A count goal optionally targets a single book type (book_type_id), so "20 books
this year" and "20 manga this year" can coexist. NULL means all books.
Uniqueness per (user, kind, book_type_id) — and for finish_by per target book or
series — is enforced in the API: SQLite treats NULLs as distinct, so a DB unique
constraint can't cover the all-books case.

Past periods are judged against the target that applied then: every target a
goal has had is a ReadingGoalTarget row (``changed_on`` = the reader's reading
day of the change). Periods that ended before the period holding ``start_date``
are "before the goal", neither met nor missed.

Delete is soft (``deleted_at``) for a short undo window; every query of live
goals filters ``deleted_at IS NULL`` (see ``live_goals``).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Query, mapped_column

from backend.core.database import Base


class ReadingGoal(Base):
    __tablename__ = "reading_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Count goals: the current target (its history is in reading_goal_targets).
    # finish_by: the number of volumes when the goal was set (the live count is
    # computed from the library on every read).
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = all books; set = only books of this type count toward the goal.
    book_type_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("book_types.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Key of the period a goal_reached notification was already created for:
    # the UTC instant of local midnight on the period's first day (the value
    # the first goals build stored for month/year windows, so an upgrade does
    # not notify a reached month twice). Cleared on target change so a raised
    # target can notify again.
    notified_window_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    # The reader's reading day the goal was set. Older rows: backfilled from
    # created_at (UTC date) by the startup migration.
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # finish_by only: what to finish, and by when (a local date, inclusive).
    target_series: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    target_book_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("books.id", ondelete="SET NULL"),
        nullable=True,
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # finish_by: set the first time the goal is seen complete (kept even if a
    # new volume is added later, so a met goal stays met).
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Soft delete for undo; purged after goals.RESTORE_WINDOW.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ReadingGoalTarget(Base):
    """One target a goal had, from the reading day it was set.

    The target for a period is the last row with ``changed_on`` on or before
    the period's last day: a change during a period applies to that whole
    period (the reader edits the goal they are in), earlier periods keep the
    target they had. Periods before the first row use the first target.
    """
    __tablename__ = "reading_goal_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reading_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


def live_goals(q: Query) -> Query:
    """Filter a ReadingGoal query to goals that are not (soft-)deleted."""
    return q.filter(ReadingGoal.deleted_at.is_(None))
