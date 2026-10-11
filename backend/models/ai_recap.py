"""Cached "Recap to here" texts for the reading companion.

One row per (book, chapter, scope). ``chapter_idx`` is the chapter the reader
is in (the recap covers the summaries of every chapter before it plus this
chapter's text up to ``position_fraction``); ``chapter_idx == chapter count``
is the whole book, which is what "Previously on" shows for the next volume.

The cache is user-independent: ``scope_hash`` is
``digest.Scope.scope_hash`` (a hash of the earlier volumes the recap folds
in), so two readers with the same read earlier volumes share a row.
``position_fraction`` is where the text the model saw ends; a cached row is
only served to a reader at or past it, so a recap never reaches past the
reader's own position (``backend/services/ai/recap.py``). ``digested`` /
``needed`` record the digest coverage it was written from; a row written
from fewer digests than now exist is stale.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AIRecap(Base):
    __tablename__ = "ai_recaps"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_idx", "scope_hash", name="uq_ai_recap"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    position_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    digested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON list of {book_id, title, series_index, missing, needed}: volumes in
    # scope that had chapters without a usable digest at write time. NULL on
    # rows from before the column (reported as []).
    missing_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
