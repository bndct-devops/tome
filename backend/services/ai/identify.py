"""Bindery identification: propose metadata for incoming files.

For each file the service gathers evidence (filename, parent folder, the
filename parser's guess, embedded metadata, a text sample from the first
pages, the canonical identity of a matching series already in the library,
and the top metadata-source candidates), sends up to ``BATCH_SIZE`` files per
model call, and returns one proposal per file with a confidence and a
one-sentence evidence. Nothing is written: the Bindery UI pre-fills its review
forms and the user accepts through the normal ``/bindery/accept`` path.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.permissions import book_visibility_filter, is_admin
from backend.models.book import Book
from backend.models.library import BookType
from backend.models.user import User
from backend.services import ai
from backend.services.ai import settings as ai_settings
from backend.services.filename_parser import parse_filename
from backend.services.metadata import extract_metadata
from backend.services.metadata_fetch import MetadataCandidate, fetch_candidates
from backend.services.sibling_match import find_series_identity
from backend.services.text_sample import first_pages_text

logger = logging.getLogger(__name__)

FEATURE = "bindery_identify"
BATCH_SIZE = 10
MAX_FILES = 50
MAX_CANDIDATES = 5
TEXT_SAMPLE_CHARS = 3000
CANDIDATE_FETCH_CONCURRENCY = 4
SAMPLE_TITLES = 5
_DESCRIPTION_CHARS = 400
# Output budget per call. Opus thinks inside max_tokens, and a batch answers
# for every file, so the budget grows with the batch.
_BASE_MAX_TOKENS = 4096
_MAX_TOKENS_PER_FILE = 1000
_MAX_TOKENS_CAP = 16000

SYSTEM_PROMPT = """\
You identify ebook files waiting in the inbox of a self-hosted library server, \
so the library owner can review and accept them.

For each file you receive evidence: the filename, its parent folder, the \
filename parser's guess, metadata embedded in the file, a text sample from the \
first pages (none for comics), the identity of a matching series already in \
the library with a few of its titles (when there is one), and up to five \
candidates from online metadata sources. You also receive the book types this \
library uses.

For every file, return one proposal with: title, author, series and \
series_index (null when the book is not part of a series), content_type \
("volume" for a book or a collected volume, "chapter" for a single chapter \
release), book_type_slug (one of the given slugs, or null when none fits), \
language (ISO 639-1 code), year of first publication, tags, confidence (0 to \
1) and evidence (one sentence naming what decided it), and \
candidate_source_id (the source_id of the candidate that is this exact book, \
or null).

Rules:
- Match a candidate only when it is this exact book: same work, same volume, \
same language and same format (a light novel is not its manga adaptation). \
The matched candidate's ISBN, cover, description and publisher are copied \
into the book, so if no candidate is this book, set candidate_source_id to \
null.
- Tags come only from the tags of the candidates. Never invent a tag.
- When a matching library series is given, use its series name and author \
spelling exactly.
- When two candidates tie, prefer the one whose ISBN matches the embedded ISBN.
- Title: when a matching library series is given with sample_titles, follow \
the title pattern most of them use, with this book's own volume number. \
Otherwise use the book's own title, without the series name or volume number \
unless that is how the book is actually titled.
- Year of first publication: take it from the embedded metadata, a candidate \
or the first pages. Use null when none of them gives it.
- If the first pages disagree with the filename, set confidence below 0.5 and \
say so in the evidence.
- Confidence means: how sure you are that every proposed field is right. Use \
0.9 or more only when independent evidence agrees.
- Return one proposal per file_id you were given, and only the JSON the schema \
describes."""


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def build_schema(type_slugs: list[str]) -> dict[str, Any]:
    """The structured-output schema for one batch. Book type slugs are an enum
    when the instance has any. No numeric bounds: structured outputs do not
    support them, so confidence is clamped after parsing."""
    slug_schema: dict[str, Any] = (
        _nullable({"type": "string", "enum": sorted(type_slugs)}) if type_slugs
        else {"type": "null"}
    )
    proposal = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "title": {"type": "string"},
            "author": _nullable({"type": "string"}),
            "series": _nullable({"type": "string"}),
            "series_index": _nullable({"type": "number"}),
            "content_type": {"type": "string", "enum": ["volume", "chapter"]},
            "book_type_slug": slug_schema,
            "language": _nullable({"type": "string"}),
            "year": _nullable({"type": "integer"}),
            "tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
            "candidate_source_id": _nullable({"type": "string"}),
        },
        "required": [
            "file_id", "title", "author", "series", "series_index", "content_type",
            "book_type_slug", "language", "year", "tags", "confidence", "evidence",
            "candidate_source_id",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"files": {"type": "array", "items": proposal}},
        "required": ["files"],
        "additionalProperties": False,
    }


# ── evidence ─────────────────────────────────────────────────────────────────

@dataclass
class FileEvidence:
    path: str                      # bindery-relative, as the client sent it
    filename: str
    folder: str | None
    format: str
    size: int
    filename_guess: dict[str, Any]
    embedded: dict[str, Any]
    text_sample: str | None
    library_series: dict[str, Any] | None
    candidates: list[MetadataCandidate] = field(default_factory=list)

    def for_model(self, file_id: str) -> dict[str, Any]:
        return {
            "file_id": file_id,
            "filename": self.filename,
            "folder": self.folder,
            "format": self.format,
            "size_bytes": self.size,
            "filename_guess": self.filename_guess,
            "embedded_metadata": self.embedded,
            "first_pages_text": self.text_sample,
            "library_series": self.library_series,
            "candidates": [_candidate_for_model(c) for c in self.candidates],
        }


def _candidate_for_model(c: MetadataCandidate) -> dict[str, Any]:
    desc = c.description or None
    if desc and len(desc) > _DESCRIPTION_CHARS:
        desc = desc[:_DESCRIPTION_CHARS].rstrip() + "..."
    return {
        "source": c.source,
        "source_id": c.source_id,
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
        "description": desc,
    }


def candidate_to_dict(c: MetadataCandidate) -> dict[str, Any]:
    """Same shape as /bindery/preview candidates, so the UI can apply it."""
    return {
        "source": c.source,
        "source_id": c.source_id,
        "title": c.title,
        "author": c.author,
        "description": c.description,
        "cover_url": c.cover_url,
        "publisher": c.publisher,
        "year": c.year,
        "page_count": c.page_count,
        "isbn": c.isbn,
        "language": c.language,
        "tags": list(c.tags or []),
        "series": c.series,
        "series_index": c.series_index,
    }


def _embedded_for_model(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k.startswith("_") or k == "cover_path" or v in (None, "", []):
            continue
        if k == "description" and isinstance(v, str) and len(v) > _DESCRIPTION_CHARS:
            v = v[:_DESCRIPTION_CHARS].rstrip() + "..."
        out[k] = v
    # ComicInfo.xml genres and the manga flag are useful evidence for comics.
    if meta.get("_genres"):
        out["genres"] = list(meta["_genres"])
    if meta.get("_is_manga"):
        out["is_manga"] = True
    return out


def series_sample_titles(db: Session, user: User | None, series: str, *,
                         exclude_id: int | None = None,
                         limit: int = SAMPLE_TITLES) -> list[dict[str, Any]]:
    """Up to ``limit`` titles (with volume numbers) of active books in
    ``series`` that ``user`` can see, reviewed books first, so the model can
    follow the series' title pattern."""
    q = db.query(Book.title, Book.series_index).filter(
        Book.series == series, Book.status == "active")
    if exclude_id is not None:
        q = q.filter(Book.id != exclude_id)
    if user is not None and not is_admin(user):
        q = q.filter(book_visibility_filter(db, user))
    rows = (
        q.order_by(Book.is_reviewed.desc(), Book.series_index.is_(None),
                   Book.series_index, Book.id)
        .limit(limit)
        .all()
    )
    rows = sorted(rows, key=lambda r: (r.series_index is None, r.series_index or 0))
    return [{"title": r.title, "series_index": r.series_index} for r in rows]


def build_evidence(db: Session, rel_path: str, full_path: Path,
                   user: User | None = None) -> FileEvidence:
    """Everything except the metadata-source candidates (fetched separately,
    concurrently, by ``attach_candidates``). ``user`` scopes the series'
    sample titles to the books that user can see."""
    rel_parts = Path(rel_path).parts
    in_chapters_dir = len(rel_parts) > 1 and rel_parts[0].lower() == "chapters"
    parsed = parse_filename(full_path.name, in_chapters_dir=in_chapters_dir)
    meta = extract_metadata(full_path, settings.covers_dir)

    identity = find_series_identity(
        db, meta.get("series") or parsed.series, meta.get("author") or parsed.author)
    library_series: dict[str, Any] | None = None
    if identity:
        bt = db.get(BookType, identity.book_type_id) if identity.book_type_id else None
        library_series = {
            "series": identity.series,
            "author": identity.author,
            "book_type_slug": bt.slug if bt else None,
            "language": identity.language,
            "volumes_in_library": identity.volume_count,
            "sample_titles": series_sample_titles(db, user, identity.series),
        }

    return FileEvidence(
        path=rel_path,
        filename=full_path.name,
        folder=rel_parts[-2] if len(rel_parts) > 1 else None,
        format=full_path.suffix.lower().lstrip("."),
        size=full_path.stat().st_size,
        filename_guess={
            "title": parsed.title,
            "author": parsed.author,
            "series": parsed.series,
            "series_index": parsed.series_index,
            "content_type": parsed.content_type,
        },
        embedded=_embedded_for_model(meta),
        text_sample=first_pages_text(full_path, TEXT_SAMPLE_CHARS),
        library_series=library_series,
    )


async def _fetch_for(ev: FileEvidence, sem: asyncio.Semaphore) -> list[MetadataCandidate]:
    lib = ev.library_series or {}
    emb = ev.embedded
    guess = ev.filename_guess
    async with sem:
        try:
            result = await fetch_candidates(
                title=emb.get("title") or guess.get("title") or Path(ev.filename).stem,
                author=lib.get("author") or emb.get("author") or guess.get("author"),
                isbn=emb.get("isbn"),
                series=lib.get("series") or emb.get("series") or guess.get("series"),
                series_index=emb.get("series_index") or guess.get("series_index"),
                language=lib.get("language") or emb.get("language"),
                media_hint=lib.get("book_type_slug"),
            )
        except Exception as exc:  # noqa: BLE001 - candidates are evidence, not a requirement
            logger.info("identify: candidate fetch failed for %s (%s)", ev.filename, exc)
            return []
    return list(result.candidates[:MAX_CANDIDATES])


def attach_candidates(evidence: list[FileEvidence]) -> None:
    async def _all() -> list[list[MetadataCandidate]]:
        sem = asyncio.Semaphore(CANDIDATE_FETCH_CONCURRENCY)
        return await asyncio.gather(*(_fetch_for(ev, sem) for ev in evidence))

    for ev, cands in zip(evidence, asyncio.run(_all())):
        ev.candidates = cands


# ── proposals ────────────────────────────────────────────────────────────────

def _str_or_none(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _sanitize(raw: dict[str, Any], ev: FileEvidence, types_by_slug: dict[str, BookType]) -> dict[str, Any]:
    """Coerce one model proposal into safe values. Tags outside the candidate
    tags are dropped; an unknown slug or candidate id becomes null."""
    title = _str_or_none(raw.get("title")) or ev.filename_guess.get("title") or Path(ev.filename).stem

    series_index = raw.get("series_index")
    series_index = float(series_index) if isinstance(series_index, (int, float)) and not isinstance(series_index, bool) else None

    year = raw.get("year")
    year = int(year) if isinstance(year, int) and not isinstance(year, bool) and 0 < year <= 2100 else None

    content_type = raw.get("content_type") if raw.get("content_type") in ("volume", "chapter") else "volume"

    slug = _str_or_none(raw.get("book_type_slug"))
    bt = types_by_slug.get(slug) if slug else None

    allowed_tags: dict[str, str] = {}
    for c in ev.candidates:
        for tag in c.tags or []:
            if isinstance(tag, str) and tag.strip():
                allowed_tags.setdefault(tag.strip().lower(), tag.strip())
    tags: list[str] = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, str):
            canonical = allowed_tags.get(tag.strip().lower())
            if canonical and canonical not in tags:
                tags.append(canonical)

    return {
        "title": title,
        "author": _str_or_none(raw.get("author")),
        "series": _str_or_none(raw.get("series")),
        "series_index": series_index,
        "content_type": content_type,
        "book_type_slug": bt.slug if bt else None,
        "book_type_id": bt.id if bt else None,
        "language": _str_or_none(raw.get("language")),
        "year": year,
        "tags": tags,
    }


def _confidence(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    return round(min(1.0, max(0.0, float(raw))), 3)


def _evidence_sentence(raw: Any) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    return text[:500] if text else "No evidence given."


def build_user_content(batch: list[FileEvidence], book_types: list[BookType]) -> list[dict[str, Any]]:
    payload = {
        "book_types": [{"slug": bt.slug, "label": bt.label} for bt in book_types],
        "files": [ev.for_model(f"f{i + 1}") for i, ev in enumerate(batch)],
    }
    return [{
        "type": "text",
        "text": "Identify these files. Evidence as JSON:\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=1),
    }]


def _identify_batch(
    db: Session, user: User, batch: list[FileEvidence], book_types: list[BookType],
) -> list[dict[str, Any]]:
    types_by_slug = {bt.slug: bt for bt in book_types}
    result = ai.run_feature(
        db, user, FEATURE,
        system=SYSTEM_PROMPT,
        user_content=build_user_content(batch, book_types),
        schema=build_schema(list(types_by_slug)),
        max_tokens=min(_MAX_TOKENS_CAP, _BASE_MAX_TOKENS + _MAX_TOKENS_PER_FILE * len(batch)),
    )
    by_id: dict[str, dict[str, Any]] = {}
    for item in (result.parsed or {}).get("files") or []:
        if isinstance(item, dict) and isinstance(item.get("file_id"), str):
            by_id.setdefault(item["file_id"], item)

    out: list[dict[str, Any]] = []
    for i, ev in enumerate(batch):
        raw = by_id.get(f"f{i + 1}")
        if raw is None:
            out.append({
                "path": ev.path, "proposal": None, "confidence": 0.0,
                "evidence": "The model returned no proposal for this file.", "candidate": None,
            })
            continue
        cand_id = _str_or_none(raw.get("candidate_source_id"))
        cand = next((c for c in ev.candidates if c.source_id == cand_id), None) if cand_id else None
        out.append({
            "path": ev.path,
            "proposal": _sanitize(raw, ev, types_by_slug),
            "confidence": _confidence(raw.get("confidence")),
            "evidence": _evidence_sentence(raw.get("evidence")),
            "candidate": candidate_to_dict(cand) if cand else None,
        })
    return out


def identify_files(db: Session, user: User, files: list[tuple[str, Path]]) -> dict[str, Any]:
    """Propose metadata for ``files`` ((bindery-relative path, resolved path)
    pairs, already safety-checked by the caller). One model call per batch of
    up to ``BATCH_SIZE`` files. Raises the typed AI errors from ``run_feature``.
    """
    # Fail fast before the slow evidence work when AI is off or no key resolves.
    ai.check_feature_available(db, FEATURE)
    ai.get_provider(db, user)

    book_types = db.query(BookType).order_by(BookType.sort_order, BookType.id).all()
    proposals: list[dict[str, Any]] = []
    for start in range(0, len(files), BATCH_SIZE):
        chunk = files[start:start + BATCH_SIZE]
        evidence = [build_evidence(db, rel, full, user) for rel, full in chunk]
        attach_candidates(evidence)
        proposals.extend(_identify_batch(db, user, evidence, book_types))
    return {"proposals": proposals, "threshold": ai_settings.confidence_threshold(db)}
