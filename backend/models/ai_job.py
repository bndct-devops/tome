"""Background AI jobs (the reading companion's digest jobs), persisted.

The in-memory job in ``backend/services/ai/digest_job.py`` drives execution;
this row mirrors it on every state change (enqueue, start, each unit done,
stop, finish) so the jobs view has history and per-book spend, and so the
automatic triggers can tell that the user's latest job for a book was
cancelled. Rows left ``queued``/``running``/``stopping`` by a restart are
marked ``interrupted`` at startup and never resumed on their own.

``cost_usd`` and ``calls`` are the sum and count of the job's ``ai_usage``
rows (``AIUsage.job_id``). The ``est_*`` fields are the estimate made when
the job expanded into units, shown next to the actual figures.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base

# queued | running | stopping | done | cancelled | stopped | error | interrupted
STATUSES = ("queued", "running", "stopping", "done", "cancelled", "stopped", "error", "interrupted")
LIVE_STATUSES = ("queued", "running", "stopping")


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="digest")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="book")  # book | series
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    upto_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)       # chapters
    done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)        # incl. failed
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)       # model calls billed
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    est_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    est_chapters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
