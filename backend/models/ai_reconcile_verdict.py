"""Remembered verdicts of the reconcile pass ("Merge duplicates").

One row per judged pair of glossary entities of a series (``entity_a`` the
lower id, ``entity_b`` the higher): what the model said (``same``,
``confidence``) and a ``stamp`` of what it saw, the sum of both entities'
note counts plus alias counts at judging time. ``reconcile.candidates``
skips a pair whose verdict's stamp still equals the pair's current stamp, so
a pair called different is not re-sent (and re-billed) on every run; a new
note or alias on either side re-opens it. A merge writes its verdict too.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AIReconcileVerdict(Base):
    __tablename__ = "ai_reconcile_verdicts"
    __table_args__ = (
        UniqueConstraint("series_key", "entity_a", "entity_b", name="uq_ai_reconcile_verdict"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    series_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_a: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_b: Mapped[int] = mapped_column(Integer, nullable=False)
    same: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stamp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
