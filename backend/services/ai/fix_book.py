"""Fix this book: propose a metadata diff for one library book.

The service gathers the book's current fields, its file names, a short text
sample from the first pages, and up to ``MAX_CANDIDATES`` candidates from the
metadata sources (the same fetch the "Fetch Metadata" dialog runs). The model
picks the candidate that is this book, if any, and proposes values for exactly
the fields ``ApplyMetadataRequest`` accepts. ``null`` means "no change".

Nothing is written. The book page opens the normal metadata diff on the
proposal, and the user applies it through ``POST /books/{id}/apply-metadata``
with ``ai_assisted: true`` so the audit entry records where it came from.

Safety rails applied after parsing, whatever the model says:
- tags only from the candidates' tags; an ISBN only from the matched
  candidate; description and cover only copied from the matched candidate;
- a value equal to the current one becomes ``null`` (no change);
- an empty tag list becomes ``null`` (applying it would wipe fetched tags).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.user import User
from backend.services import ai
from backend.services.ai import settings as ai_settings
from backend.services.ai.identify import candidate_to_dict, series_sample_titles
from backend.services.metadata_fetch import MetadataCandidate, fetch_candidates
from backend.services.text_sample import first_pages_text

logger = logging.getLogger(__name__)

FEATURE = "fix_book"
MAX_CANDIDATES = 8
TEXT_SAMPLE_CHARS = 2000
_DESCRIPTION_CHARS = 600

# The fields ApplyMetadataRequest accepts, in diff order.
FIELDS = (
    "title", "author", "description", "publisher", "year", "language", "isbn",
    "series", "series_index", "tags", "cover_url",
)

SYSTEM_PROMPT = """\
You fix the metadata of one book in a self-hosted ebook library. The owner \
reviews your proposal as a diff and decides what to apply.

You receive the book's current metadata, its file names, a text sample from \
its first pages (none for comics), a few titles of other books in the same \
series in this library (series_siblings, when it is in a series), and up to \
eight candidates from online metadata sources, each with a candidate_id.

Decide which candidate, if any, is this exact book (same work, same volume, \
same language), then propose values for: title, author, publisher, year (of \
first publication), language (ISO 639-1 code), isbn, series, series_index, \
tags, use_candidate_description and use_candidate_cover. Also return \
candidate_id (the candidate you matched, or null), confidence (0 to 1) and \
evidence (one sentence naming what decided it).

Rules:
- null means "keep the current value". Use null for every field you have no \
good reason to change. Never propose a value just to restate the current one.
- Keep the current series name and author spelling unless they are clearly \
wrong; the library may use them across many books.
- Tags come only from the candidates' tags. Never invent a tag. Use null when \
the current tags are fine or no candidate has useful tags.
- The isbn must be the matched candidate's ISBN; use null when candidate_id \
is null. When two candidates tie, prefer the one whose ISBN matches the \
current ISBN.
- Title: when series_siblings are given, follow the title pattern most of \
them use, with this book's own volume number. Otherwise use the book's own \
title, without the series name or volume number unless that is how the book \
is actually titled.
- use_candidate_description: true only when the matched candidate's \
description is better than the current one (the current one is missing, \
truncated or for another book). You never write a description yourself.
- use_candidate_cover: true only when the book has no cover, or the matched \
candidate is clearly the same edition and the current cover looks wrong.
- If no candidate is this book, set candidate_id to null, only fix what the \
current data and the first pages prove, and keep confidence below 0.5.
- If the first pages disagree with the current title or author, say so in \
the evidence and keep confidence below 0.5.
- Confidence means: how sure you are that every proposed change is right. \
Use 0.9 or more only when independent evidence agrees.
- Return only the JSON the schema describes."""


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def build_schema() -> dict[str, Any]:
    """Structured-output schema. No numeric bounds (structured outputs do not
    support them), so confidence and year are checked after parsing."""
    nullable_str = _nullable({"type": "string"})
    return {
        "type": "object",
        "properties": {
            "candidate_id": nullable_str,
            "title": nullable_str,
            "author": nullable_str,
            "publisher": nullable_str,
            "year": _nullable({"type": "integer"}),
            "language": nullable_str,
            "isbn": nullable_str,
            "series": nullable_str,
            "series_index": _nullable({"type": "number"}),
            "tags": _nullable({"type": "array", "items": {"type": "string"}}),
            "use_candidate_description": {"type": "boolean"},
            "use_candidate_cover": {"type": "boolean"},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
        },
        "required": [
            "candidate_id", "title", "author", "publisher", "year", "language", "isbn",
            "series", "series_index", "tags", "use_candidate_description",
            "use_candidate_cover", "confidence", "evidence",
        ],
        "additionalProperties": False,
    }


# ── evidence ─────────────────────────────────────────────────────────────────

def current_fields(book: Book) -> dict[str, Any]:
    """The book's values for every field the proposal can change."""
    return {
        "title": book.title,
        "author": book.author,
        "description": book.description,
        "publisher": book.publisher,
        "year": book.year,
        "language": book.language,
        "isbn": book.isbn,
        "series": book.series,
        "series_index": book.series_index,
        "tags": [t.tag for t in book.tags],
        "cover_url": None,  # covers are local files; the UI shows "Has cover"
    }


def _truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _candidate_for_model(cid: str, c: MetadataCandidate) -> dict[str, Any]:
    return {
        "candidate_id": cid,
        "source": c.source,
        "title": c.title,
        "author": c.author,
        "series": c.series,
        "series_index": c.series_index,
        "year": c.year,
        "isbn": c.isbn,
        "language": c.language,
        "publisher": c.publisher,
        "page_count": c.page_count,
        "tags": list(c.tags or []),
        "has_cover": bool(c.cover_url),
        "description": _truncate(c.description, _DESCRIPTION_CHARS),
    }


def _text_sample(book: Book) -> str | None:
    for f in book.files:
        path = Path(f.file_path)
        try:
            if not path.is_file():
                continue
            sample = first_pages_text(path, TEXT_SAMPLE_CHARS)
        except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
            logger.info("fix_book: text sample failed for book %s (%s)", book.id, exc)
            continue
        if sample:
            return sample
    return None


def build_user_content(book: Book, candidates: list[MetadataCandidate],
                       text_sample: str | None,
                       siblings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    current = current_fields(book)
    current.pop("cover_url")
    current["description"] = _truncate(current["description"], _DESCRIPTION_CHARS)
    current["has_cover"] = bool(book.cover_path)
    current["book_type"] = book.book_type.label if book.book_type else None
    current["content_type"] = book.content_type
    payload = {
        "book": current,
        "files": [Path(f.file_path).name for f in book.files],
        "first_pages_text": text_sample,
        "series_siblings": siblings or [],
        "candidates": [_candidate_for_model(f"c{i + 1}", c) for i, c in enumerate(candidates)],
    }
    return [{
        "type": "text",
        "text": "Fix this book's metadata. Evidence as JSON:\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=1),
    }]


def _fetch(book: Book, query: str | None) -> list[MetadataCandidate]:
    try:
        result = asyncio.run(fetch_candidates(
            title=book.title,
            author=book.author,
            isbn=book.isbn,
            series=book.series,
            series_index=book.series_index,
            query_override=query,
            year=book.year,
            language=book.language,
            media_hint=book.book_type.slug if book.book_type else None,
        ))
    except Exception as exc:  # noqa: BLE001 - the model can still fix from the book alone
        logger.info("fix_book: candidate fetch failed for book %s (%s)", book.id, exc)
        return []
    return list(result.candidates[:MAX_CANDIDATES])


# ── proposal ─────────────────────────────────────────────────────────────────

def _str_or_none(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _isbn_digits(v: str | None) -> str:
    return re.sub(r"[^0-9Xx]", "", v or "").upper()


def _confidence(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    return round(min(1.0, max(0.0, float(raw))), 3)


def _evidence_sentence(raw: Any) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    return text[:500] if text else "No evidence given."


def sanitize(raw: dict[str, Any], book: Book, candidates: list[MetadataCandidate],
             cand: MetadataCandidate | None) -> dict[str, Any]:
    """Coerce the model's answer into a safe proposal. Every field is present;
    ``None`` means no change."""
    current = current_fields(book)
    out: dict[str, Any] = {k: None for k in FIELDS}

    for key in ("title", "author", "publisher", "series"):
        val = _str_or_none(raw.get(key))
        if val is not None and val != current[key]:
            out[key] = val

    lang = _str_or_none(raw.get("language"))
    if lang is not None:
        lang = lang.lower()
        if len(lang) <= 8 and lang != (current["language"] or "").lower():
            out["language"] = lang

    year = raw.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 0 < year <= 2100 and year != current["year"]:
        out["year"] = year

    idx = raw.get("series_index")
    if isinstance(idx, (int, float)) and not isinstance(idx, bool) and idx >= 0:
        idx = float(idx)
        if current["series_index"] is None or float(current["series_index"]) != idx:
            out["series_index"] = idx

    # ISBN: only the matched candidate's ISBN, stored in its form. Another
    # candidate's ISBN is another edition (or another book).
    isbn = _isbn_digits(_str_or_none(raw.get("isbn")))
    if isbn and isbn != _isbn_digits(current["isbn"]) \
            and cand is not None and cand.isbn and _isbn_digits(cand.isbn) == isbn:
        out["isbn"] = cand.isbn

    # Tags: candidate tags only, canonical spelling; null when nothing changes.
    allowed: dict[str, str] = {}
    for c in candidates:
        for tag in c.tags or []:
            if isinstance(tag, str) and tag.strip():
                allowed.setdefault(tag.strip().lower(), tag.strip())
    raw_tags = raw.get("tags")
    if isinstance(raw_tags, list):
        tags: list[str] = []
        for tag in raw_tags:
            if isinstance(tag, str):
                canonical = allowed.get(tag.strip().lower())
                if canonical and canonical not in tags:
                    tags.append(canonical)
        if tags and {t.lower() for t in tags} != {t.lower() for t in current["tags"]}:
            out["tags"] = tags

    if cand is not None:
        if raw.get("use_candidate_description") is True and cand.description \
                and cand.description.strip() != (current["description"] or "").strip():
            out["description"] = cand.description
        if raw.get("use_candidate_cover") is True and cand.cover_url:
            out["cover_url"] = cand.cover_url

    return out


def fix_book(db: Session, user: User, book: Book, *, query: str | None = None) -> dict[str, Any]:
    """Propose a metadata diff for ``book`` (visibility already checked by the
    caller). Raises the typed AI errors from ``run_feature``."""
    # Fail fast before the slow candidate fetch when AI is off or no key resolves.
    ai.check_feature_available(db, FEATURE)
    ai.get_provider(db, user)

    candidates = _fetch(book, _str_or_none(query))
    siblings = (series_sample_titles(db, user, book.series, exclude_id=book.id)
                if book.series else [])
    result = ai.run_feature(
        db, user, FEATURE,
        system=SYSTEM_PROMPT,
        user_content=build_user_content(book, candidates, _text_sample(book), siblings),
        schema=build_schema(),
    )
    raw = result.parsed if isinstance(result.parsed, dict) else {}

    by_id = {f"c{i + 1}": c for i, c in enumerate(candidates)}
    cand = by_id.get(_str_or_none(raw.get("candidate_id")) or "")
    proposal = sanitize(raw, book, candidates, cand)

    return {
        "proposal": proposal,
        "changed_fields": [k for k in FIELDS if proposal[k] is not None],
        "confidence": _confidence(raw.get("confidence")),
        "evidence": _evidence_sentence(raw.get("evidence")),
        "candidate_source": cand.source if cand else None,
        "candidate": candidate_to_dict(cand) if cand else None,
        "current": {**current_fields(book), "has_cover": bool(book.cover_path)},
        "threshold": ai_settings.confidence_threshold(db),
    }
