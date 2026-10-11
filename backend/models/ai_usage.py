"""AI usage ledger: one row per provider call, with tokens and computed cost.

Cost is computed at insert from the price table in
``backend/services/ai/usage.py`` so a later price change never rewrites
history. ``user_id`` is SET NULL on user deletion so instance spend totals
stay correct after an account is removed.

``job_id`` names the background job a call belonged to (``ai_jobs.id``, no
FK: the ledger outlives anything), ``book_id`` the book it was about (the
volume a digest read, the book a recap or Recall was for; SET NULL when the
book goes). ``duration_ms`` is how long the provider took, for the digest
time estimate. ``words_sent``/``chapters`` describe a digest call's input
for the output estimate. All are nullable and were added after run 1
(startup ALTER TABLE in ``backend/main.py``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    key_source: Mapped[str] = mapped_column(String(16), nullable=False)  # user | instance
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)
    book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Digest calls: the words of chapter text sent and how many chapters the
    # call covered (the estimate's output history). NULL for other calls.
    words_sent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chapters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user: Mapped[Optional["User"]] = relationship("User")
