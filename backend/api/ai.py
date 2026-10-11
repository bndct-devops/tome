"""AI provider settings, keys and the usage meter (/api/ai/*).

Feature endpoints (identify, fix, series cleanup) live under the same router
and call ``backend.services.ai.run_feature``; its typed errors are mapped to
HTTP statuses by the handler registered in ``register_exception_handlers``.

Rules: no key is ever returned or logged (status carries ``has_own_key`` and
a masked suffix); guests get 403 everywhere except ``GET /status``;
TOME_AI_ENABLED=false makes everything except ``GET /status`` a 404.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.bindery import SUPPORTED_EXTENSIONS as BINDERY_EXTENSIONS, _require_bindery, _safe_resolve
from backend.core.database import get_db
from backend.core.permissions import is_admin, require_role, user_can_see_book
from backend.core.security import get_current_user
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai
from backend.services import book_text
from backend.services.ai import (
    companion, digest, digest_job, fix_book, identify, recall as ai_recall, recap as ai_recap,
    registry, series_cleanup, settings as ai_settings, usage,
)
from backend.services.ai.provider import AIError, AIKeyInvalid, AIProviderError
from backend.services.audit import audit

router = APIRouter(prefix="/ai", tags=["ai"])


def require_env_enabled() -> None:
    if not ai_settings.env_enabled():
        raise HTTPException(status_code=404, detail="AI features are disabled")


def require_ai_user(current_user: User = Depends(get_current_user)) -> User:
    """Members and admins only. Use on every AI endpoint except GET /status."""
    require_env_enabled()
    require_role(current_user, "member")
    return current_user


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _validate_key(api_key: str) -> None:
    """Raise HTTPException for a key the provider will not accept."""
    try:
        ai.make_provider(api_key).validate_key()
    except AIKeyInvalid as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.message)


# ── status ───────────────────────────────────────────────────────────────────

@router.get("/status")
def ai_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return ai_settings.status_payload(db, current_user)


# ── own key ──────────────────────────────────────────────────────────────────

class KeyBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


@router.put("/key")
def set_own_key(
    body: KeyBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    key = body.api_key.strip()
    if not key:
        raise HTTPException(status_code=422, detail="Key must not be empty")
    _validate_key(key)
    replaced = bool(current_user.ai_api_key)
    ai_settings.set_user_key(db, current_user, key)
    db.commit()
    audit(db, "ai.key_set", user_id=current_user.id, username=current_user.username,
          resource_type="user", resource_id=current_user.id,
          details={"provider": registry.PROVIDER, "replaced": replaced}, ip=_ip(request))
    return ai_settings.status_payload(db, current_user)


@router.delete("/key")
def remove_own_key(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    had_key = bool(current_user.ai_api_key)
    ai_settings.set_user_key(db, current_user, None)
    db.commit()
    if had_key:
        audit(db, "ai.key_removed", user_id=current_user.id, username=current_user.username,
              resource_type="user", resource_id=current_user.id,
              details={"provider": registry.PROVIDER}, ip=_ip(request))
    return ai_settings.status_payload(db, current_user)


# ── instance settings (admin) ────────────────────────────────────────────────

class FeatureSettings(BaseModel):
    enabled: bool | None = None
    # A model id from registry.MODELS, or "" / "default" to clear the override.
    model: str | None = None


class InstanceBody(BaseModel):
    enabled: bool | None = None
    instance_key: str | None = Field(default=None, max_length=512)
    clear_instance_key: bool = False
    share_instance_key: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    features: dict[str, FeatureSettings] | None = None


@router.put("/instance")
def update_instance(
    body: InstanceBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Validate everything before writing anything.
    for key, fs in (body.features or {}).items():
        if key not in registry.FEATURES:
            raise HTTPException(status_code=422, detail=f"Unknown AI feature: {key}")
        if fs.model not in (None, "", "default") and fs.model not in registry.MODELS:
            raise HTTPException(status_code=422, detail=f"Unknown model: {fs.model}")
    new_key = (body.instance_key or "").strip() or None
    if new_key and body.clear_instance_key:
        raise HTTPException(status_code=422, detail="Send either instance_key or clear_instance_key, not both")
    if new_key:
        _validate_key(new_key)

    changed: list[str] = []
    if body.enabled is not None and body.enabled != ai_settings.instance_enabled(db):
        ai_settings.set_instance_enabled(db, body.enabled)
        changed.append("enabled")
    if new_key:
        ai_settings.set_instance_key(db, new_key)
        changed.append("instance_key")
    elif body.clear_instance_key and ai_settings.get_db_instance_key(db):
        ai_settings.set_instance_key(db, None)
        changed.append("instance_key_cleared")
    if body.share_instance_key is not None and body.share_instance_key != ai_settings.share_instance_key(db):
        ai_settings.set_share_instance_key(db, body.share_instance_key)
        changed.append("share_instance_key")
    if body.confidence_threshold is not None and body.confidence_threshold != ai_settings.confidence_threshold(db):
        ai_settings.set_confidence_threshold(db, body.confidence_threshold)
        changed.append("confidence_threshold")
    for key, fs in (body.features or {}).items():
        if fs.enabled is not None and fs.enabled != ai_settings.feature_enabled(db, key):
            ai_settings.set_feature_enabled(db, key, fs.enabled)
            changed.append(f"features.{key}.enabled")
        if fs.model is not None:
            model = None if fs.model in ("", "default") else fs.model
            if model != ai_settings.feature_model_override(db, key):
                ai_settings.set_feature_model(db, key, model)
                changed.append(f"features.{key}.model")
    db.commit()

    if changed:
        # Field names only. Never the values of keys.
        audit(db, "ai.instance_settings_changed", user_id=current_user.id,
              username=current_user.username, resource_type="instance",
              details={"fields": changed}, ip=_ip(request))
    return ai_settings.status_payload(db, current_user)


# ── usage meter ──────────────────────────────────────────────────────────────

@router.get("/usage")
def get_usage(
    month: str | None = Query(default=None, description="YYYY-MM, default the current UTC month"),
    all_users: bool = Query(default=False, alias="all", description="Admins: totals across all users"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    if all_users and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        return usage.month_summary(db, month=month, user_id=current_user.id, all_users=all_users)
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")


# ── bindery identify ─────────────────────────────────────────────────────────

class IdentifyBody(BaseModel):
    # Bindery-relative paths, same rules as the /bindery endpoints.
    paths: list[str] = Field(min_length=1, max_length=identify.MAX_FILES)


@router.post("/identify")
def identify_bindery_files(
    body: IdentifyBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Propose metadata for Bindery files. Writes nothing: the UI pre-fills
    its review forms and the user accepts through /bindery/accept."""
    _require_bindery(current_user)
    files: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for rel in body.paths:
        if rel in seen:
            continue
        seen.add(rel)
        full = _safe_resolve(rel)
        if not full.is_file() or full.suffix.lower() not in BINDERY_EXTENSIONS:
            raise HTTPException(status_code=404, detail=f"File not found: {rel}")
        files.append((rel, full))
    return identify.identify_files(db, current_user, files)


# ── fix this book ────────────────────────────────────────────────────────────

class FixBookBody(BaseModel):
    # Optional search override, same semantics as GET /books/{id}/fetch-metadata?q=
    query: str | None = Field(default=None, max_length=300)


@router.post("/books/{book_id}/fix")
def fix_one_book(
    book_id: int,
    body: FixBookBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Propose a metadata diff for one book. Writes nothing: the book page
    shows the diff and applies through /books/{id}/apply-metadata with
    ai_assisted=true."""
    book = db.get(Book, book_id)
    if book is None or not user_can_see_book(db, current_user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return fix_book.fix_book(db, current_user, book, query=body.query if body else None)


# ── clean up this series ─────────────────────────────────────────────────────
# {name:path} so a series name containing "/" still routes; the static
# suffixes keep the two routes apart.

@router.post("/series/{name:path}/cleanup")
def propose_series_cleanup(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Propose one diff for a series (name, volume titles and numbers, status,
    arcs). Writes nothing: the series view applies the checked rows through
    /series/{name}/cleanup/apply."""
    try:
        return series_cleanup.propose_cleanup(db, current_user, name)
    except series_cleanup.SeriesNotFound:
        raise HTTPException(status_code=404, detail="Series not found")
    except series_cleanup.SeriesTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class CleanupBookChange(BaseModel):
    book_id: int
    # null = keep the current value
    title: str | None = Field(default=None, max_length=1000)
    series_index: float | None = None


class CleanupArc(BaseModel):
    name: str = Field(min_length=1, max_length=series_cleanup.MAX_NAME_CHARS)
    start_index: float
    end_index: float
    description: str | None = Field(default=None, max_length=2000)


class CleanupApplyBody(BaseModel):
    # Each part is optional: send only what the user kept checked.
    series_name: str | None = Field(default=None, max_length=series_cleanup.MAX_NAME_CHARS)
    books: list[CleanupBookChange] = Field(default_factory=list, max_length=series_cleanup.MAX_BOOKS)
    status: str | None = None
    # The full new arc list; null leaves the arcs alone.
    arcs: list[CleanupArc] | None = Field(default=None, max_length=200)


@router.post("/series/{name:path}/cleanup/apply")
def apply_series_cleanup(
    name: str,
    body: CleanupApplyBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Apply the checked parts of a cleanup proposal in one transaction, via
    the same code paths as the manual book, series status and arc editors."""
    ai.check_feature_available(db, series_cleanup.FEATURE)
    try:
        result = series_cleanup.apply_cleanup(
            db, current_user, name,
            series_name=body.series_name,
            books=[b.model_dump() for b in body.books],
            status=body.status,
            arcs=[a.model_dump() for a in body.arcs] if body.arcs is not None else None,
        )
    except series_cleanup.CleanupApplyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    audit(db, "ai.series_cleanup_applied", user_id=current_user.id,
          username=current_user.username, resource_type="series", resource_title=name,
          details={
              "renamed_to": result["series_name"] if result["renamed"] else None,
              "books_updated": result["books_updated"],
              "books_moved": result["books_moved"],
              "status_changed": result["status_changed"],
              "arcs_set": result["arcs_set"],
          }, ip=_ip(request))
    return result


# ── reading companion: digest jobs ───────────────────────────────────────────
# Jobs are in memory (backend/services/ai/digest_job.py). A job is visible to
# the user it runs for and to admins; anyone else gets 404.

def _visible_job(job_id: str, user: User) -> dict[str, Any]:
    job = digest_job.get(job_id)
    if job is None or (job["user_id"] != user.id and not is_admin(user)):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/digest-jobs/{job_id}")
def get_digest_job(
    job_id: str,
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Progress of a digest job: ``{id, status, done, total, current, ...}``."""
    return _visible_job(job_id, current_user)


@router.post("/digest-jobs/{job_id}/cancel")
def cancel_digest_job(
    job_id: str,
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Cancel a queued job, or a running one before its next chapter. The
    chapters already digested stay stored."""
    _visible_job(job_id, current_user)
    job = digest_job.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── reading companion: recap, glossary, Recall ───────────────────────────────
# Spoiler window: everything is built from the digests of the chapters before
# the reader's position chapter plus that chapter's text up to the position
# (backend/services/ai/companion.py). An explicit ?chapter= may not be past
# the position chapter.

def _visible_book(db: Session, user: User, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if book is None or book.status != "active" or not user_can_see_book(db, user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def _web_window(db: Session, user: User, book: Book, chapter: int | None) -> companion.Window:
    try:
        return companion.web_window(db, user, book, chapter)
    except companion.WindowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


def _iso(dt: Any) -> str | None:
    return dt.isoformat() + "Z" if dt is not None else None


@router.get("/books/{book_id}/companion")
def get_companion(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """What the book page needs to decide whether to show the reading
    companion, and its header line. Never calls a model or queues work."""
    book = _visible_book(db, current_user, book_id)
    feats = companion.features_for(db, current_user)
    chapters = book_text.book_chapters(book, db)
    out: dict[str, Any] = {
        "available": False, "reason": None, "features": feats, "position": None,
        "chapters": len(chapters), "coverage": None, "job": None, "recap_cached": False,
        "glossary_count": 0, "previously": None, "scope": None,
    }
    if (not ai_settings.ai_enabled(db) or not ai_settings.feature_enabled(db, digest.FEATURE)
            or not (ai_settings.feature_enabled(db, ai_recap.FEATURE)
                    or ai_settings.feature_enabled(db, ai_recall.FEATURE))):
        out["reason"] = companion.FEATURE_OFF
        return out
    out["job"] = digest_job.active_for(current_user.id, book.id)
    pos = companion.reader_position(db, current_user, book)
    pos_k = companion.position_chapter(book, pos.word_pct, db) if pos and chapters else None
    if feats["recap"] and (pos is None or not pos_k):
        prev = ai_recap.previously_for(db, current_user, book)
        if prev is not None:
            out["previously"] = {"book_id": prev[0].id, "title": prev[0].title}
    reason = book_text.unavailable_reason(book, db)
    if reason:
        out["reason"] = reason
        return out
    if ai_settings.resolve_key(db, current_user) is None:
        out["reason"] = companion.NO_KEY
        return out
    if pos is None:
        out["reason"] = companion.NO_POSITION
        return out
    window = _web_window(db, current_user, book, None)
    scope = digest.user_scope(db, current_user, book)
    out.update({
        "available": True,
        "position": {
            "pct": pos.pct, "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title,
            "source": pos.source, "updated_at": _iso(pos.updated_at),
        },
        "coverage": digest.coverage(db, scope, window.chapter_idx),
        "recap_cached": ai_recap.cached_for(db, current_user, book, window, scope) is not None,
        "glossary_count": len(digest.glossary_state(db, current_user, book, window.chapter_idx,
                                                     scope=scope)),
        "scope": scope.to_dict(),
    })
    return out


class RecapBody(BaseModel):
    # The chapter the recap ends in; default (and maximum) the position chapter.
    chapter: int | None = Field(default=None, ge=0)
    # Write the recap from the digests that exist even when some are missing
    # (after a digest job that could not read every chapter).
    partial: bool = False


@router.post("/books/{book_id}/recap")
def post_recap(
    book_id: int,
    body: RecapBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> Any:
    """200 ``{text, chapter_idx, chapter_title, whole_book, cached, partial,
    created_at, scope}``, or 202 ``{job, coverage, chapter_idx,
    chapter_title}`` while the chapters before the position are digested."""
    body = body or RecapBody()
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    window = _web_window(db, current_user, book, body.chapter)
    status, payload = ai_recap.request_recap(db, current_user, book, window, partial=body.partial)
    return JSONResponse(status_code=status, content=payload)


@router.delete("/books/{book_id}/recap")
def delete_recap(
    book_id: int,
    chapter: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Clear the cached recap at this chapter ("Ask again"); the next POST
    writes a new one."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    window = _web_window(db, current_user, book, chapter)
    return {"deleted": ai_recap.clear(db, current_user, book, window.chapter_idx),
            "chapter_idx": window.chapter_idx}


@router.get("/books/{book_id}/glossary")
def get_glossary(
    book_id: int,
    chapter: int | None = Query(default=None, ge=0),
    queue: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """The glossary up to the position chapter (from storage, no model call).
    Queues the missing digests when the user has a key; ``job`` carries the
    progress. ``queue=false`` only reads: the web UI refreshes with it after a
    job ends, so a chapter that cannot be read is not retried in a loop."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    window = _web_window(db, current_user, book, chapter)
    return companion.glossary_payload(db, current_user, book, window, enqueue=queue)


class RecallBody(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    chapter: int | None = Field(default=None, ge=0)


@router.post("/books/{book_id}/recall")
def post_recall(
    book_id: int,
    body: RecallBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """``{found, live, entry?, message?, term, chapter_idx, chapter_title}``."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recall.FEATURE)
    window = _web_window(db, current_user, book, body.chapter)
    try:
        return ai_recall.recall(db, current_user, book, window, body.term)
    except ai_recall.BadTerm as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/books/{book_id}/previously")
def get_previously(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """The whole-book recap of the previous volume ("Previously on"), written
    when that volume was marked read. 404 when there is no earlier volume the
    user read, or nothing was written."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    prev = ai_recap.previously_for(db, current_user, book)
    if prev is None:
        raise HTTPException(status_code=404, detail="No recap of an earlier volume")
    return ai_recap.previously_dict(*prev)


# ── error mapping ────────────────────────────────────────────────────────────

def _ai_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AIError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AIError, _ai_error_handler)
