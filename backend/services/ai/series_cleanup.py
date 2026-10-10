"""Clean up this series: propose one diff for a whole series.

The service gathers every book in the series the caller can see (title,
author, volume number, year, content type, file names), the series' current
publication status and its arcs. The model proposes a canonical series name,
per-book title / volume-number fixes, a publication status and an arc list
that covers the volumes without overlap, each with a one-sentence evidence.

Nothing is written by the proposal. The series view renders it as checkable
rows and posts the checked parts to ``apply_cleanup``, which writes them in
one transaction through the same code paths the manual editors use
(``services/book_updates.py`` and ``services/series_meta.py``) and writes one
``ai.series_cleanup_applied`` audit entry.

Safety rails applied after parsing, whatever the model says:
- only books in the caller's visible set; unknown ids are dropped;
- a value equal to the current one becomes ``null`` (no change);
- a status outside the known set, or arcs that overlap or run backwards, are
  dropped from the proposal instead of being shown.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from backend.core.permissions import book_visibility_filter, is_admin
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai
from backend.services import series_meta as series_meta_service
from backend.services.ai import settings as ai_settings
from backend.services.book_updates import apply_book_update, bulk_update_books, can_edit_book
from backend.services.series_meta import VALID_STATUSES, ArcError

FEATURE = "series_cleanup"
MAX_BOOKS = 200
MAX_NAME_CHARS = 255
NO_SERIES_SENTINEL = "__unserialized__"
_STATUSES = ("ongoing", "finished", "hiatus", "unknown")

SYSTEM_PROMPT = """\
You clean up one series in a self-hosted ebook library. The owner reviews \
your proposal as a diff, row by row, and decides what to apply.

You receive the series name as the library stores it, every book in it (id, \
title, author, volume number, year, content type, file names), the stored \
publication status and the stored arcs (named ranges of volume numbers).

Propose:
- series_name: the canonical name of the series, or null to keep it. Fix \
only clear problems: stray volume numbers or format words in the name, \
inconsistent capitalisation, a misspelling. Keep the library's spelling when \
it is a legitimate variant (a translation, a romanisation).
- books: only the books that need a change. For each, the book_id, a \
corrected title (or null to keep it) and a corrected series_index (or null \
to keep it). A volume number is wrong when the title, the file names or the \
order of publication say otherwise; two books sharing a number, or a gap that \
the titles explain, are typical signs. Titles should follow one consistent \
pattern across the series, matching the pattern most books already use. \
Never change a title only to restate it.
- status: the publication status (ongoing, finished, hiatus, unknown) when \
you know it, or null to keep the stored one.
- arcs: the full arc list for the series when you know its story arcs, or \
null to keep the stored arcs. Arcs use the volume numbers after your \
corrections, run from start_index to end_index inclusive, never overlap, and \
use the arc names the series itself uses. Description is one short sentence \
without spoilers, or null.
- Each part carries one sentence of evidence naming what decided it.
- confidence (0 to 1): how sure you are that every proposed change is right. \
Use 0.9 or more only when the evidence is unambiguous. Keep it below 0.5 when \
you do not recognise the series and are working from the file names alone.
- Use null for anything you have no good reason to change.
- Return only the JSON the schema describes."""


# ── schema ───────────────────────────────────────────────────────────────────

def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def build_schema() -> dict[str, Any]:
    """Structured-output schema. No numeric bounds (structured outputs do not
    support them), so indices and confidence are checked after parsing."""
    nullable_str = _nullable({"type": "string"})
    book = {
        "type": "object",
        "properties": {
            "book_id": {"type": "integer"},
            "title": nullable_str,
            "series_index": _nullable({"type": "number"}),
            "evidence": {"type": "string"},
        },
        "required": ["book_id", "title", "series_index", "evidence"],
        "additionalProperties": False,
    }
    arc = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "start_index": {"type": "number"},
            "end_index": {"type": "number"},
            "description": nullable_str,
        },
        "required": ["name", "start_index", "end_index", "description"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "series_name": nullable_str,
            "series_name_evidence": {"type": "string"},
            "books": {"type": "array", "items": book},
            "status": _nullable({"type": "string", "enum": list(_STATUSES)}),
            "status_evidence": {"type": "string"},
            "arcs": _nullable({"type": "array", "items": arc}),
            "arcs_evidence": {"type": "string"},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
        },
        "required": [
            "series_name", "series_name_evidence", "books", "status", "status_evidence",
            "arcs", "arcs_evidence", "confidence", "evidence",
        ],
        "additionalProperties": False,
    }


# ── evidence ─────────────────────────────────────────────────────────────────

def visible_series_books(db: Session, user: User, name: str) -> list[Book]:
    """Every book in the series the user can see, in volume order."""
    return (
        db.query(Book)
        .filter(Book.status == "active", Book.series == name, book_visibility_filter(db, user))
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc(), Book.id.asc())
        .all()
    )


def _arc_dict(arc: Any) -> dict[str, Any]:
    return {
        "name": arc.name,
        "start_index": float(arc.start_index),
        "end_index": float(arc.end_index),
        "description": arc.description or None,
    }


def current_arcs(db: Session, name: str) -> list[dict[str, Any]]:
    return [_arc_dict(a) for a in series_meta_service.list_arcs(db, name)]


def build_user_content(name: str, books: Sequence[Book], status: str,
                       arcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {
        "series_name": name,
        "status": status,
        "arcs": arcs,
        "books": [
            {
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "series_index": b.series_index,
                "year": b.year,
                "content_type": b.content_type,
                "book_type": b.book_type.label if b.book_type else None,
                "files": [Path(f.file_path).name for f in b.files],
            }
            for b in books
        ],
    }
    return [{
        "type": "text",
        "text": "Clean up this series. Evidence as JSON:\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=1),
    }]


# ── proposal ─────────────────────────────────────────────────────────────────

def _str_or_none(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _confidence(raw: Any) -> float:
    v = _number(raw)
    return 0.0 if v is None else round(min(1.0, max(0.0, v)), 3)


def _evidence(raw: Any, fallback: str = "No evidence given.") -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    return text[:500] if text else fallback


def _same_index(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and float(a) == float(b)


def _arcs_key(arcs: list[dict[str, Any]]) -> list[tuple]:
    return sorted((a["name"], a["start_index"], a["end_index"], a["description"] or "") for a in arcs)


def _sanitize_arcs(raw: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """(arcs, problem). ``None`` arcs means the proposal keeps the stored ones."""
    if not isinstance(raw, list):
        return None, None
    arcs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _str_or_none(item.get("name"))
        start, end = _number(item.get("start_index")), _number(item.get("end_index"))
        if name is None or start is None or end is None or start < 0:
            continue
        name = name[:MAX_NAME_CHARS]
        if name.lower() in seen:
            return None, "The proposed arcs repeat a name, so they were left out."
        if start > end:
            return None, "A proposed arc ends before it starts, so the arcs were left out."
        seen.add(name.lower())
        desc = _str_or_none(item.get("description"))
        arcs.append({"name": name, "start_index": start, "end_index": end,
                     "description": desc[:500] if desc else None})
    arcs.sort(key=lambda a: (a["start_index"], a["end_index"]))
    for prev, cur in zip(arcs, arcs[1:]):
        if cur["start_index"] <= prev["end_index"]:
            return None, "The proposed arcs overlap, so they were left out."
    return arcs, None


def can_rename_series(db: Session, user: User, name: str) -> bool:
    """A rename touches every book in the series, so a member may rename only
    a series whose books are all theirs. Admins always may."""
    if is_admin(user):
        return True
    books = db.query(Book).filter(Book.series == name).all()
    return bool(books) and all(can_edit_book(user, b) for b in books)


def build_proposal(db: Session, user: User, name: str, books: Sequence[Book],
                   raw: dict[str, Any]) -> dict[str, Any]:
    """Turn the model's answer into the diff the UI renders."""
    status_now = series_meta_service.series_status(db, name)
    arcs_now = current_arcs(db, name)

    proposed_name = _str_or_none(raw.get("series_name"))
    if proposed_name is not None:
        proposed_name = proposed_name[:MAX_NAME_CHARS]
        if proposed_name == name or proposed_name == NO_SERIES_SENTINEL:
            proposed_name = None

    by_id = {b.id: b for b in books}
    book_rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw.get("books") or []:
        if not isinstance(item, dict):
            continue
        bid = item.get("book_id")
        if isinstance(bid, bool) or not isinstance(bid, int) or bid in seen or bid not in by_id:
            continue
        book = by_id[bid]
        title = _str_or_none(item.get("title"))
        if title is not None and title == book.title:
            title = None
        idx = _number(item.get("series_index"))
        if idx is not None and (idx < 0 or _same_index(idx, book.series_index)):
            idx = None
        if title is None and idx is None:
            continue
        seen.add(bid)
        book_rows.append({
            "book_id": bid,
            "current": {"title": book.title, "series_index": book.series_index},
            "proposed": {"title": title, "series_index": idx},
            "evidence": _evidence(item.get("evidence")),
            "editable": can_edit_book(user, book),
        })
    order = {b.id: i for i, b in enumerate(books)}
    book_rows.sort(key=lambda r: order[r["book_id"]])

    status = raw.get("status")
    if not isinstance(status, str) or status not in VALID_STATUSES or status == status_now:
        status = None

    arcs, arc_problem = _sanitize_arcs(raw.get("arcs"))
    if arcs is not None and _arcs_key(arcs) == _arcs_key(arcs_now):
        arcs = None

    admin = is_admin(user)
    return {
        "series": name,
        "book_count": len(books),
        "series_name": {
            "current": name,
            "proposed": proposed_name,
            "evidence": _evidence(raw.get("series_name_evidence")),
        },
        "books": book_rows,
        "status": {
            "current": status_now,
            "proposed": status,
            "evidence": _evidence(raw.get("status_evidence")),
        },
        "arcs": {
            "current": arcs_now,
            "proposed": arcs,
            "evidence": arc_problem or _evidence(raw.get("arcs_evidence")),
        },
        "confidence": _confidence(raw.get("confidence")),
        "evidence": _evidence(raw.get("evidence")),
        "threshold": ai_settings.confidence_threshold(db),
        # What this user may apply. Status and arcs follow the admin-only
        # series editors; a rename needs every book in the series to be theirs.
        "permissions": {
            "series_name": can_rename_series(db, user, name),
            "status": admin,
            "arcs": admin,
        },
    }


class SeriesTooLarge(ValueError):
    pass


class SeriesNotFound(LookupError):
    pass


def propose_cleanup(db: Session, user: User, name: str) -> dict[str, Any]:
    """Ask the model for a cleanup diff of the series ``name``. Writes
    nothing. Raises SeriesNotFound / SeriesTooLarge and the typed AI errors."""
    ai.check_feature_available(db, FEATURE)
    if not name or name == NO_SERIES_SENTINEL:
        raise SeriesNotFound("Series not found")
    books = visible_series_books(db, user, name)
    if not books:
        raise SeriesNotFound("Series not found")
    if len(books) > MAX_BOOKS:
        raise SeriesTooLarge(f"This series has more than {MAX_BOOKS} books, too many to clean up in one go.")
    ai.get_provider(db, user)  # fail fast with 409 before building the prompt

    result = ai.run_feature(
        db, user, FEATURE,
        system=SYSTEM_PROMPT,
        user_content=build_user_content(
            name, books, series_meta_service.series_status(db, name), current_arcs(db, name),
        ),
        schema=build_schema(),
        # Thinking shares max_tokens; leave room for a long series.
        max_tokens=min(12000, 4096 + 60 * len(books)),
    )
    raw = result.parsed if isinstance(result.parsed, dict) else {}
    return build_proposal(db, user, name, books, raw)


# ── apply ────────────────────────────────────────────────────────────────────

class CleanupApplyError(Exception):
    """A rejected apply. ``status_code`` is the HTTP status to answer with."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _Arc:
    def __init__(self, name: str, start_index: float, end_index: float,
                 description: Optional[str]) -> None:
        self.name = name
        self.start_index = start_index
        self.end_index = end_index
        self.description = description


def apply_cleanup(
    db: Session,
    user: User,
    name: str,
    *,
    series_name: Optional[str] = None,
    books: Sequence[dict[str, Any]] = (),
    status: Optional[str] = None,
    arcs: Optional[Sequence[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Apply the parts of a (possibly user-edited) proposal the user kept.

    ``books`` items are ``{book_id, title?, series_index?}``; ``arcs`` is the
    full new arc list or ``None`` to leave arcs alone. Everything is
    validated before anything is written, then written in one transaction.
    The caller writes the audit entry and commits."""
    if not name or name == NO_SERIES_SENTINEL:
        raise CleanupApplyError(404, "Series not found")
    visible = visible_series_books(db, user, name)
    if not visible:
        raise CleanupApplyError(404, "Series not found")
    by_id = {b.id: b for b in visible}

    # ── validate everything first ────────────────────────────────────────────
    new_name = series_name.strip() if isinstance(series_name, str) else None
    if series_name is not None and not new_name:
        raise CleanupApplyError(400, "Series name must not be empty")
    if new_name == name:
        new_name = None
    if new_name is not None:
        if new_name == NO_SERIES_SENTINEL or len(new_name) > MAX_NAME_CHARS:
            raise CleanupApplyError(400, "Invalid series name")
        if not can_rename_series(db, user, name):
            raise CleanupApplyError(403, "Renaming a series needs edit rights on every book in it")

    edits: list[tuple[Book, dict[str, Any]]] = []
    seen: set[int] = set()
    for item in books:
        bid = item.get("book_id")
        book = by_id.get(bid) if isinstance(bid, int) else None
        if book is None:
            raise CleanupApplyError(404, f"Book {bid} is not in this series")
        if bid in seen:
            raise CleanupApplyError(400, f"Book {bid} appears twice")
        seen.add(bid)
        data: dict[str, Any] = {}
        if item.get("title") is not None:
            title = str(item["title"]).strip()
            if not title:
                raise CleanupApplyError(400, "Title must not be empty")
            if title != book.title:
                data["title"] = title
        if item.get("series_index") is not None:
            idx = float(item["series_index"])
            if idx < 0:
                raise CleanupApplyError(400, "series_index must not be negative")
            if not _same_index(idx, book.series_index):
                data["series_index"] = idx
        if not data:
            continue
        if not can_edit_book(user, book):
            raise CleanupApplyError(403, "You can only edit books you uploaded")
        edits.append((book, data))

    if status is not None:
        if status not in VALID_STATUSES:
            raise CleanupApplyError(400, f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        if not is_admin(user):
            raise CleanupApplyError(403, "Only admins can change a series' status")

    arc_objs: list[_Arc] | None = None
    if arcs is not None:
        if not is_admin(user):
            raise CleanupApplyError(403, "Only admins can change a series' arcs")
        arc_objs = []
        names: set[str] = set()
        for a in arcs:
            arc_name = str(a.get("name") or "").strip()
            if not arc_name:
                raise CleanupApplyError(400, "Arc name must not be empty")
            if arc_name in names:
                raise CleanupApplyError(400, f"Arc '{arc_name}' appears twice")
            names.add(arc_name)
            desc = a.get("description")
            arc_objs.append(_Arc(arc_name, float(a["start_index"]), float(a["end_index"]),
                                 str(desc).strip() or None if desc is not None else None))
        try:
            for arc in arc_objs:
                series_meta_service.validate_arc_indices(arc.start_index, arc.end_index)
            series_meta_service.check_no_overlap(arc_objs)
        except ArcError as exc:
            raise CleanupApplyError(400, str(exc))

    if not edits and new_name is None and status is None and arc_objs is None:
        raise CleanupApplyError(422, "Nothing to apply")

    # ── write (the caller commits) ───────────────────────────────────────────
    for book, data in edits:
        apply_book_update(db, book, data)

    moved = 0
    target = name
    if new_name is not None:
        series_books = db.query(Book).filter(Book.series == name).all()
        bulk_update_books(db, series_books, series=new_name)
        series_meta_service.move_series_meta(db, name, new_name)
        moved = len(series_books)
        target = new_name

    if status is not None:
        series_meta_service.set_series_status(db, target, status)
    if arc_objs is not None:
        series_meta_service.sync_arcs(db, target, arc_objs)

    return {
        "series_name": target,
        "renamed": new_name is not None,
        "books_updated": len(edits),
        "books_moved": moved,
        "status": series_meta_service.series_status(db, target),
        "arcs": current_arcs(db, target),
        "status_changed": status is not None,
        "arcs_set": len(arc_objs) if arc_objs is not None else None,
    }
