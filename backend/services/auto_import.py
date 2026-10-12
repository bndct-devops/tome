"""Unattended Bindery import (``TOME_AUTO_IMPORT``), one tick of the loop in
``backend/main.py``.

Per tick:

0. Collect supported files in the inbox. Files the Bindery review page holds
   (``bindery_review.is_held``) are skipped; files already in the library (by
   content hash or path) are skipped before any paid work.
1. AI identify (only when a key resolves for the acting user): one
   ``identify.identify_files`` call per ``identify.BATCH_SIZE`` files,
   Bindery-style; a failing batch keeps the earlier batches' proposals. Files
   that vanished or got held since phase 0 are dropped right before each
   call. Proposals at or above ``ai.confidence_threshold`` set title / author
   / series / series_index / content_type / book_type_id / year / language,
   and the import audit entry carries ``ai_assisted``. Any AI failure falls
   back to the fetch-first path; it never blocks the import.

   Billing guard: every proposal is cached in memory by content hash for
   ``PROPOSAL_TTL_SECONDS`` and reused instead of a new call, so a file held
   between identify and ingest is not identified again next tick. A file whose
   ingest failed goes into ``_failed`` for the same TTL: the AI phase skips it
   and the next ticks retry the ingest with its cached proposal, if any, and
   never a new model call. In-memory and single-process like
   ``bindery_review``; a restart forgets both.
2. Ingest (sync, in a thread): move, extract, create the Book
   (``is_reviewed=False``). Right before the move the file is left for the
   next tick when its size or mtime changed since phase 0 (still being
   copied in) or a Bindery review now holds it.
3. Enrich (async fetch, sync apply in a thread): a fetch candidate is applied
   only when it ``agrees`` (``identity_match``) with the file's own identity
   (the AI proposal when one was applied, else embedded metadata, else the
   filename). Otherwise the book keeps its own data and waits in the
   unreviewed queue.

The acting user is the first admin (lowest id): imported books are
``added_by`` that admin, and the AI path resolves that admin's key (own key,
else the instance key, which admins may always use). No admin = no AI and
``added_by`` NULL.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.services import bindery_review
from backend.services.identity_match import agrees
from backend.services.metadata_fetch import MetadataCandidate, fetch_candidates
from backend.services.safe_fetch import UnsafeURLError, fetch_safe_image

logger = logging.getLogger("backend.main")

SUPPORTED = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}

# How long a model proposal (and a failed-ingest mark) is kept per content hash.
PROPOSAL_TTL_SECONDS = 6 * 3600

SessionFactory = Callable[[], ContextManager[Session]]


# ── identity agreement ───────────────────────────────────────────────────────

@dataclass
class Identity:
    """What the file says it is: the applied AI proposal, else the embedded
    metadata, else the filename."""
    title: str | None
    author: str | None = None
    series_index: float | None = None


# ``agrees`` is ``identity_match.agrees``, imported above (kept as a module
# name for callers).


# ── proposal cache and failed-ingest marks ───────────────────────────────────

_cache_lock = threading.Lock()
_proposals: dict[str, tuple[dict[str, Any], float]] = {}
_failed: dict[str, float] = {}

# Indirection so tests can move the clock.
_now = time.monotonic


def _fresh(at: float) -> bool:
    return _now() - at < PROPOSAL_TTL_SECONDS


def _cached_proposal(content_hash: str) -> dict[str, Any] | None:
    with _cache_lock:
        hit = _proposals.get(content_hash)
        if hit is None:
            return None
        if _fresh(hit[1]):
            return hit[0]
        del _proposals[content_hash]
        return None


def _remember_proposal(content_hash: str, prop: dict[str, Any]) -> None:
    with _cache_lock:
        _proposals[content_hash] = (prop, _now())


def _mark_failed(content_hash: str) -> None:
    with _cache_lock:
        _failed[content_hash] = _now()


def _is_failed(content_hash: str) -> bool:
    with _cache_lock:
        at = _failed.get(content_hash)
        if at is None:
            return False
        if _fresh(at):
            return True
        del _failed[content_hash]
        return False


def _forget(content_hash: str) -> None:
    with _cache_lock:
        _proposals.pop(content_hash, None)
        _failed.pop(content_hash, None)


def reset() -> None:
    """Clear the proposal cache and the failed marks (tests)."""
    with _cache_lock:
        _proposals.clear()
        _failed.clear()


# ── helpers ──────────────────────────────────────────────────────────────────

@dataclass
class Summary:
    found: int = 0
    held: int = 0
    deferred: int = 0
    duplicates: int = 0
    imported: int = 0
    ai_applied: int = 0
    enriched: int = 0
    gated: int = 0
    errors: int = 0
    ai_error: str | None = None
    imported_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class _Pending:
    """A new file from phase 0, with the size and mtime it had then."""
    path: Path
    content_hash: str
    size: int
    mtime_ns: int


class _Deferred(Exception):
    """Leave the file in the inbox for the next tick (``reason``: held|changed)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Ingested:
    book_id: int
    title: str
    author: str | None
    isbn: str | None
    series: str | None
    series_index: float | None
    content_hash: str
    content_type: str | None
    media_hint: str | None
    language: str | None
    ai_applied: bool
    ai_candidate: dict[str, Any] | None


def cleanup_empty_dir(start: Path, stop_at: Path) -> None:
    """Walk upward from start removing empty dirs, never removing stop_at itself."""
    current = start.resolve()
    stop = stop_at.resolve()
    while current != stop and current != current.parent:
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
            else:
                break
        except OSError:
            break
        current = current.parent


def _acting_user(db: Session):
    from backend.models.user import User
    return (db.query(User)
            .filter(User.is_admin == True)  # noqa: E712
            .order_by(User.id)
            .first())


def _default_session_factory() -> SessionFactory:
    from backend.core.database import SessionLocal
    return SessionLocal


def _collect(incoming: Path) -> list[Path]:
    out: list[Path] = []
    for p in incoming.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(incoming)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.suffix.lower() in SUPPORTED:
            out.append(p)
    return sorted(out)


# ── phase 0: dedupe ──────────────────────────────────────────────────────────

def _dedupe(session_factory: SessionFactory, files: list[Path]) -> list[_Pending]:
    """The files not yet in the library, with their hash, size and mtime."""
    from backend.models.book import Book, BookFile
    from backend.services.metadata import sha256_file

    keep: list[_Pending] = []
    seen: set[str] = set()
    with session_factory() as db:
        for file_path in files:
            try:
                st = file_path.stat()
                content_hash = sha256_file(file_path)
            except OSError as exc:
                logger.warning("Auto-import: cannot read %s: %s", file_path.name, exc)
                continue
            existing = db.query(Book).filter(Book.content_hash == content_hash).first()
            if existing:
                logger.info("Auto-import: skipping %s - already imported as book #%d",
                            file_path.name, existing.id)
                continue
            if db.query(BookFile).filter(BookFile.file_path == str(file_path.resolve())).first():
                logger.info("Auto-import: skipping %s - file path already registered", file_path.name)
                continue
            if content_hash in seen:
                logger.info("Auto-import: skipping %s - same file earlier in this batch", file_path.name)
                continue
            seen.add(content_hash)
            keep.append(_Pending(file_path, content_hash, st.st_size, st.st_mtime_ns))
    return keep


# ── phase 1: AI identify ─────────────────────────────────────────────────────

def _identify(session_factory: SessionFactory, incoming: Path,
              pending: list[_Pending], summary: Summary) -> tuple[dict[str, dict[str, Any]], float]:
    """Proposals by relative path, and the threshold. Empty when no key
    resolves or AI is off: the fetch-first path takes over. Cached proposals
    are reused without a call; files marked failed are never sent; the rest
    go out one ``identify.BATCH_SIZE`` batch at a time, and a failing batch
    keeps what the earlier batches returned."""
    from backend.services.ai import check_feature_available, identify
    from backend.services.ai import settings as ai_settings
    from backend.services.ai.provider import AIDisabled, AIError, AINotConfigured

    if not pending:
        return {}, 1.0
    proposals: dict[str, dict[str, Any]] = {}
    with session_factory() as db:
        user = _acting_user(db)
        if user is None or ai_settings.resolve_key(db, user) is None:
            return {}, 1.0
        try:
            check_feature_available(db, identify.FEATURE)
        except AIDisabled as exc:
            logger.info("Auto-import: AI identify unavailable (%s), using metadata fetch", exc)
            return {}, 1.0
        threshold = float(ai_settings.confidence_threshold(db))

        need: list[_Pending] = []
        for p in pending:
            rel = str(p.path.relative_to(incoming))
            cached = _cached_proposal(p.content_hash)
            if cached is not None:
                proposals[rel] = {**cached, "path": rel}
            elif not _is_failed(p.content_hash):
                need.append(p)

        for start in range(0, len(need), identify.BATCH_SIZE):
            batch: list[_Pending] = []
            for p in need[start:start + identify.BATCH_SIZE]:
                rel = str(p.path.relative_to(incoming))
                if not p.path.is_file() or bindery_review.is_held(rel):
                    continue  # gone, or a review took it while earlier batches ran
                batch.append(p)
            if not batch:
                continue
            by_rel = {str(p.path.relative_to(incoming)): p for p in batch}
            try:
                out = identify.identify_files(db, user, [(rel, p.path) for rel, p in by_rel.items()])
            except (AIDisabled, AINotConfigured) as exc:
                logger.info("Auto-import: AI identify unavailable (%s), using metadata fetch", exc)
                break
            except AIError as exc:
                summary.ai_error = type(exc).__name__
                logger.warning("Auto-import: AI identify failed (%s: %s), using metadata fetch",
                               type(exc).__name__, exc)
                break
            except Exception:  # noqa: BLE001 - AI never blocks the import
                summary.ai_error = "error"
                logger.exception("Auto-import: AI identify crashed, using metadata fetch")
                break
            threshold = float(out.get("threshold", threshold))
            for prop in out.get("proposals") or []:
                if not isinstance(prop, dict) or prop.get("path") not in by_rel:
                    continue
                proposals[prop["path"]] = prop
                _remember_proposal(by_rel[prop["path"]].content_hash, prop)
    return proposals, threshold


# ── phase 2: ingest ──────────────────────────────────────────────────────────

def _ingest_file(session_factory: SessionFactory, incoming: Path, file_path: Path,
                 content_hash: str, proposal: dict[str, Any] | None,
                 seen: tuple[int, int] | None = None) -> _Ingested | None:
    """Move, extract metadata, create the Book row. ``proposal`` is an AI
    proposal already past the threshold, or None. ``seen`` is (size,
    mtime_ns) from phase 0; raises ``_Deferred`` instead of moving a file
    that changed since then or that a Bindery review now holds."""
    from backend.models.book import Book, BookFile, BookTag
    from backend.models.library import BookType
    from backend.services.audit import audit
    from backend.services.book_types import assign_book_to_type_library
    from backend.services.chapters import replace_book_chapters
    from backend.services.filename_parser import parse_filename
    from backend.services.metadata import extract_metadata
    from backend.services.organizer import get_library_path, resolve_unique_path
    from backend.services.wish_matcher import match_on_book_created

    with session_factory() as db:
        admin_user = _acting_user(db)
        added_by_id: int | None = admin_user.id if admin_user else None
        admin_username: str | None = admin_user.username if admin_user else None

        suffix = file_path.suffix.lower().lstrip(".")

        # The phase-0 check ran before the AI call; something else (a Bindery
        # accept, a scan) may have filed the same bytes since.
        if db.query(Book).filter(Book.content_hash == content_hash).first():
            logger.info("Auto-import: skipping %s - imported meanwhile", file_path.name)
            return None

        # Detect chapters/ subdir and parse filename for series/index/content_type
        rel_parts = file_path.relative_to(incoming).parts
        in_chapters_dir = len(rel_parts) > 1 and rel_parts[0].lower() == "chapters"
        parsed = parse_filename(file_path.name, in_chapters_dir=in_chapters_dir)

        meta = extract_metadata(file_path, settings.covers_dir)

        # NOTE: sibling-identity inheritance is deliberately NOT applied here.
        # Unattended auto-import must not rewrite what a file claims about
        # itself based on fuzzy matching; the sibling matcher only powers the
        # human-confirmed Bindery review. The AI proposal is the exception:
        # it is applied only at or above the admin's confidence threshold.

        # Merge: AI proposal wins, then embedded metadata, then the filename.
        meta_series_index = meta.get("series_index")
        title = meta.get("title") or parsed.title or file_path.stem
        author = meta.get("author") or parsed.author
        series = meta.get("series") or parsed.series
        series_index = meta_series_index if meta_series_index is not None else parsed.series_index
        content_type = parsed.content_type
        book_type_id: int | None = None
        language = meta.get("language")
        year = meta.get("year")
        if proposal:
            title = proposal.get("title") or title
            author = proposal.get("author") or author
            series = proposal.get("series") or series
            if proposal.get("series_index") is not None:
                series_index = proposal["series_index"]
            content_type = proposal.get("content_type") or content_type
            language = proposal.get("language") or language
            if proposal.get("year") is not None:
                year = proposal["year"]
            bt_id = proposal.get("book_type_id")
            if bt_id and db.get(BookType, bt_id):
                book_type_id = bt_id

        rel_lib = get_library_path({**meta, "title": title, "author": author,
                                    "series": series, "series_index": series_index},
                                   file_path.name)
        # Last checks before the file leaves the inbox: still being written
        # (size or mtime moved since phase 0), or a review opened meanwhile.
        if seen is not None:
            try:
                st = file_path.stat()
            except OSError:
                raise _Deferred("changed") from None
            if (st.st_size, st.st_mtime_ns) != seen:
                raise _Deferred("changed")
        if bindery_review.is_held(str(file_path.relative_to(incoming))):
            raise _Deferred("held")

        dest = resolve_unique_path(settings.library_dir, rel_lib)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), str(dest))
        logger.info("Auto-import: moved %s -> %s", file_path.name, dest)

        book = Book(
            title=title,
            author=author,
            series=series,
            series_index=series_index,
            isbn=meta.get("isbn"),
            publisher=meta.get("publisher"),
            description=meta.get("description"),
            language=language,
            year=year,
            word_count=meta.get("word_count"),
            page_count=meta.get("page_count"),
            cover_path=meta.get("cover_path"),
            content_hash=content_hash,
            content_type=content_type,
            book_type_id=book_type_id,
            status="active",
            added_by=added_by_id,
            is_reviewed=False,
        )
        db.add(book)
        db.flush()

        replace_book_chapters(db, book.id, meta.get("_chapters"))

        db.add(BookFile(
            book_id=book.id,
            file_path=str(dest.resolve()),
            format=suffix,
            file_size=dest.stat().st_size,
            content_hash=content_hash,
        ))

        # Auto-assign book type
        if not book.book_type_id:
            slug = None
            if in_chapters_dir or meta.get("_is_manga"):
                slug = "manga"
            elif suffix in ("cbz", "cbr"):
                slug = "comic"
            if slug:
                bt = db.query(BookType).filter(BookType.slug == slug).first()
                if bt:
                    book.book_type_id = bt.id

        # Genre tags from ComicInfo
        for genre in meta.get("_genres") or []:
            db.add(BookTag(book_id=book.id, tag=genre, source="comic_info"))

        db.commit()

        if book.book_type_id:
            bt = db.get(BookType, book.book_type_id)
            if bt:
                assign_book_to_type_library(db, book, bt)

        db.refresh(book)

        # Wishlist matcher - flag open wishes for admin review (no auto-fulfil)
        match_on_book_created(db, book)

        details: dict[str, Any] = {"format": suffix, "source": "auto_import"}
        if proposal:
            details["ai_assisted"] = True
        audit(
            db,
            "auto_import.imported",
            user_id=added_by_id,
            username=admin_username or "system",
            resource_type="book",
            resource_id=book.id,
            resource_title=book.title,
            details=details,
        )

        cleanup_empty_dir(file_path.parent, incoming)

        logger.info("Auto-import: imported book #%d '%s'%s (is_reviewed=False)",
                    book.id, book.title, " from the AI proposal" if proposal else "")

        return _Ingested(
            book_id=book.id, title=book.title, author=book.author, isbn=book.isbn,
            series=book.series, series_index=book.series_index,
            content_hash=content_hash, content_type=book.content_type,
            media_hint=book.book_type.slug if book.book_type else None,
            language=book.language,
            ai_applied=proposal is not None,
            ai_candidate=(proposal or {}).get("_candidate"),
        )


# ── phase 3: enrich ──────────────────────────────────────────────────────────

def _apply_metadata(session_factory: SessionFactory, book_id: int,
                    best: MetadataCandidate, content_hash: str,
                    keep_identity: bool, ai_picked: bool) -> bool:
    """Apply an agreeing candidate. ``keep_identity``: the AI proposal set
    title/author/series, so the candidate only fills the other fields.
    ``ai_picked``: the model chose this record against the file's own pages,
    which stands in for the confidence tier. Returns whether anything was
    applied."""
    from backend.models.book import Book, BookTag
    from backend.services.metadata_rank import ScoreContext, score_candidate
    from backend.services.sibling_match import apply_tier

    with session_factory() as db:
        book = db.get(Book, book_id)
        if not book:
            return False
        # Second guard on top of ``agrees``: the confidence tiers in
        # sibling_match. A strong match applies fully, a middling one only
        # fills prose gaps, a weak one is discarded.
        confidence = score_candidate(best, ScoreContext(
            title=book.title, author=book.author, isbn=book.isbn,
            year=book.year, language=book.language,
            series=book.series, series_index=book.series_index,
            media_hint=book.book_type.slug if book.book_type else None,
        ))
        tier = "full" if ai_picked else apply_tier(confidence)
        if tier == "discard":
            logger.info("Auto-import: discarding low-confidence match (score %d) for book #%d "
                        "- raw metadata kept for review", confidence, book_id)
            return False
        if keep_identity:
            pass  # title/author stay as the AI proposal set them
        elif tier == "full":
            book.title = best.title or book.title
            book.author = best.author or book.author
        else:
            logger.info("Auto-import: mid-confidence match (score %d) for book #%d - "
                        "filling prose gaps only", confidence, book_id)
        if best.description and not book.description:
            book.description = best.description
        if best.publisher and not book.publisher:
            book.publisher = best.publisher
        if best.year and not book.year:
            book.year = best.year
        if tier != "full":
            db.commit()
            return True
        if best.isbn and not book.isbn:
            book.isbn = best.isbn
        if best.language and not book.language:
            book.language = best.language
        if best.series and not book.series:
            book.series = best.series
        if best.series_index is not None and book.series_index is None:
            book.series_index = best.series_index
        if best.cover_url and not book.cover_path:
            try:
                cover_data = asyncio.run(fetch_safe_image(best.cover_url))
                from backend.services.metadata import save_cover
                book.cover_path = save_cover(cover_data, settings.covers_dir, content_hash)
            except (UnsafeURLError, Exception) as exc:  # noqa: BLE001
                logger.warning("Auto-import: failed to download cover for %s: %s", book.title, exc)
        for tag_str in best.tags or []:
            tag_str = tag_str.strip()
            if tag_str:
                db.add(BookTag(book_id=book.id, tag=tag_str, source=best.source))
        db.commit()
        logger.info("Auto-import: applied metadata from %s for book #%d '%s'",
                    best.source, book.id, book.title)
        return True


def _candidate_from_dict(d: dict[str, Any] | None) -> MetadataCandidate | None:
    if not isinstance(d, dict) or not d.get("title"):
        return None
    fields = MetadataCandidate.__dataclass_fields__
    try:
        return MetadataCandidate(**{k: v for k, v in d.items() if k in fields})
    except TypeError:
        return None


async def _enrich(session_factory: SessionFactory, ing: _Ingested, summary: Summary) -> None:
    identity = Identity(title=ing.title, author=ing.author, series_index=ing.series_index)

    # The AI's own pick among the candidates it saw, when it agrees.
    picked = _candidate_from_dict(ing.ai_candidate) if ing.ai_applied else None
    if picked is not None and not agrees(identity, picked):
        picked = None
    ai_picked = picked is not None

    if picked is None:
        fetch_result = await fetch_candidates(
            title=ing.title,
            author=ing.author,
            isbn=ing.isbn,
            series=ing.series,
            series_index=ing.series_index,
            language=ing.language,
            media_hint=ing.media_hint,
        )
        candidates = [c for c in fetch_result.candidates if c.title]
        picked = next((c for c in candidates if agrees(identity, c)), None)
        if picked is None:
            if candidates:
                summary.gated += 1
                first = candidates[0]
                logger.warning(
                    "Auto-import: no fetch candidate agrees with book #%d '%s' by %s "
                    "(first was '%s' by %s from %s); keeping the file's own metadata, "
                    "left for review", ing.book_id, ing.title, ing.author or "unknown",
                    first.title, first.author or "unknown", first.source)
            return

    applied = await asyncio.to_thread(
        _apply_metadata, session_factory, ing.book_id, picked, ing.content_hash,
        ing.ai_applied, ai_picked)
    if applied:
        summary.enriched += 1


# ── the tick ─────────────────────────────────────────────────────────────────

async def run_auto_import(session_factory: SessionFactory | None = None) -> dict[str, Any]:
    """One auto-import tick. Returns the summary (also logged)."""
    session_factory = session_factory or _default_session_factory()
    summary = Summary()

    incoming = settings.incoming_dir
    if not incoming.exists():
        return summary.as_dict()

    files = await asyncio.to_thread(_collect, incoming)
    if not files:
        return summary.as_dict()
    summary.found = len(files)

    free: list[Path] = []
    for p in files:
        rel = str(p.relative_to(incoming))
        if bindery_review.is_held(rel):
            summary.held += 1
            logger.info("Auto-import: skipping %s - open in a Bindery review", rel)
        else:
            free.append(p)

    todo = await asyncio.to_thread(_dedupe, session_factory, free)
    summary.duplicates = len(free) - len(todo)
    if not todo:
        if summary.held:
            logger.info("Auto-import: %s", summary.as_dict())
        return summary.as_dict()

    logger.info("Auto-import: %d new file(s) in the bindery", len(todo))

    from backend.services.ai import identify as _identify_mod
    proposals, threshold = await asyncio.to_thread(
        _identify, session_factory, incoming, todo[:_identify_mod.MAX_FILES], summary)
    if proposals and len(todo) > _identify_mod.MAX_FILES:
        # Keep one tick's AI spend bounded; the rest wait for the next tick
        # and get identified then, rather than slipping through unidentified.
        logger.info("Auto-import: %d file(s) left for the next tick",
                    len(todo) - _identify_mod.MAX_FILES)
        todo = todo[:_identify_mod.MAX_FILES]

    for item in todo:
        file_path, content_hash = item.path, item.content_hash
        rel = str(file_path.relative_to(incoming))
        # A review may have opened while this tick was identifying.
        if bindery_review.is_held(rel):
            summary.held += 1
            logger.info("Auto-import: skipping %s - open in a Bindery review", rel)
            continue
        prop = proposals.get(rel)
        proposal: dict[str, Any] | None = None
        if prop and prop.get("proposal") and float(prop.get("confidence") or 0.0) >= threshold:
            proposal = {**prop["proposal"], "_candidate": prop.get("candidate")}
        elif prop and prop.get("proposal"):
            logger.info("Auto-import: AI proposal for %s below threshold (%.2f < %.2f), "
                        "using metadata fetch", rel, float(prop.get("confidence") or 0.0), threshold)
        try:
            ing = await asyncio.to_thread(
                _ingest_file, session_factory, incoming, file_path, content_hash, proposal,
                (item.size, item.mtime_ns))
        except _Deferred as d:
            if d.reason == "held":
                summary.held += 1
                logger.info("Auto-import: skipping %s - open in a Bindery review", rel)
            else:
                summary.deferred += 1
                logger.info("Auto-import: %s changed since it was read, next tick", rel)
            continue
        except Exception:  # noqa: BLE001
            summary.errors += 1
            # No new model call for this file while the mark lasts (see the
            # module docstring): the ingest is retried with the cached proposal.
            _mark_failed(content_hash)
            logger.exception("Auto-import: failed to import %s", file_path.name)
            continue
        _forget(content_hash)
        if ing is None:
            continue
        summary.imported += 1
        summary.imported_ids.append(ing.book_id)
        if ing.ai_applied:
            summary.ai_applied += 1

        # Chapters skip the external fetch: the sources return wrong volumes.
        if ing.content_type == "chapter":
            continue
        try:
            await _enrich(session_factory, ing, summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-import: metadata fetch failed for %s: %s", ing.title, exc)

    logger.info("Auto-import: %s", {k: v for k, v in summary.as_dict().items() if k != "imported_ids"})
    return summary.as_dict()
