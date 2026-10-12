"""Glossary entities for the reading companion: who and what a series is about.

An ``AIEntity`` is one person, place or term of a series (``series_key``: the
series name casefolded and whitespace-collapsed, ``book:{id}`` for a
standalone), with a canonical name (its first spelling), the other names it
goes by (``aliases_json``) and one stable ``identity`` line, the one its
first chapter gave it. An ``AIEntityNote`` is what one chapter says about
it: a ``line`` (what it did there), the ``identity`` as that chapter
presents it, the ``facts`` that chapter established or changed, and the
verbatim ``evidence`` quote. Both are written by the digest call
(``backend/services/ai/digest.py``) in the same transaction as the chapter's
``AIChapterDigest`` row; what a reader sees is a fold over the notes inside
their window (``digest.profiles``).

Merges: when a chapter proves two entities are one (``same_as``), the later
one points at the earlier (``merged_into``) from that chapter on
(``merged_book_id`` / ``merged_chapter_idx``); its notes are re-pointed and
keep ``origin_entity_id``, so a reader before the merging chapter still sees
them apart.

Context ends: a multi-chapter digest call reads chapters past the one it
writes, so everything entity-level it decides (the identity, an alias, a
merge) records that call's last chapter (``identity_context_end``,
``merged_context_end``, ``context_end`` in each alias item; a single-chapter
call records its own chapter). A reader of the current book sees such a
decision only when that chapter is before their position, as for the digest
row itself (``digest.row_usable``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AIEntity(Base):
    __tablename__ = "ai_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    series_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # [{"name", "book_id", "chapter_idx", "context_end"}]: where each other
    # name was learned (and the last chapter the call that learned it read),
    # so a reader's profile only lists the ones inside their window.
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    identity: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The last chapter (of ``first_book_id``) the call that wrote ``identity``
    # read; None: written by a single-chapter call (or before this column).
    identity_context_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    first_book_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    first_chapter_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    merged_into: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The chapter whose ``same_as`` merged this entity (spoiler window).
    merged_book_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    merged_chapter_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The last chapter (of ``merged_book_id``) the merging call read.
    merged_context_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Who made the merge: None = a chapter's ``same_as`` (undone when that
    # chapter is digested again, ``digest._EntityWriter._reset_at``),
    # "reconcile" = the reconcile pass (kept across a re-read).
    merged_by: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AIEntityNote(Base):
    __tablename__ = "ai_entity_notes"
    __table_args__ = (
        UniqueConstraint("entity_id", "book_id", "chapter_idx", name="uq_ai_entity_note"),
        Index("ix_ai_entity_notes_book_chapter", "book_id", "chapter_idx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The entity the note was written for, when a merge re-pointed it.
    origin_entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    line: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Who or what the entity is as this chapter presents it (stable, never
    # an event): a reader without the first chapter still gets an identity.
    identity: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facts_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
