"""Per-chapter digests for the reading companion (recap, glossary, Recall).

A digest is the model's reading of ONE chapter: a short summary for the recap
roll-up and the glossary delta for that chapter (``entries_json``: a list of
``{name, kind, line, more, evidence}``, every ``evidence`` a verbatim quote
checked against the chapter text before storage).

Digests are user-independent: one row per (book, chapter), computed once.
Which digests a user's glossary or recap folds over is decided at query time
(``backend/services/ai/digest.py:user_scope``). ``model == "none"`` marks a
chapter with too little text to digest (no model call, no usage row).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AIChapterDigest(Base):
    __tablename__ = "ai_chapter_digests"
    __table_args__ = (UniqueConstraint("book_id", "chapter_idx", name="uq_ai_chapter_digest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The last chapter the call that wrote this row saw (a multi-chapter call:
    # its last chapter). NULL or == chapter_idx: the call saw nothing later.
    # A reader whose position chapter is <= this value must not use the row
    # (``digest.row_usable``); it is re-read singly for them and overwritten.
    context_end_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
