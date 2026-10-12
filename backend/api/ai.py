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
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.bindery import SUPPORTED_EXTENSIONS as BINDERY_EXTENSIONS, _require_bindery, _safe_resolve
from backend.core.database import get_db
from backend.core.permissions import is_admin, is_member_or_above, require_role, user_can_see_book
from backend.core.security import get_current_user
from backend.models.ai_job import AIJob
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai
from backend.services import book_text
from backend.services.ai import (
    budget, companion, digest, digest_job, fix_book, identify, jobs_view, recall as ai_recall,
    recap as ai_recap, registry, reread, series_cleanup, settings as ai_settings, usage,
    volume_recap,
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
    # Monthly cap (USD) on spend charged to the instance key. Sending null
    # removes the cap; leaving the field out keeps it.
    budget_instance_monthly_usd: float | None = Field(default=None, ge=0.0, le=1_000_000.0)


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
    budget_change: tuple[float | None, float | None] | None = None
    if "budget_instance_monthly_usd" in body.model_fields_set:
        old = ai_settings.instance_monthly_budget(db)
        new = body.budget_instance_monthly_usd
        if old != new:
            ai_settings.set_instance_monthly_budget(db, new)
            budget_change = (old, new)
    db.commit()

    if changed:
        # Field names only. Never the values of keys.
        audit(db, "ai.instance_settings_changed", user_id=current_user.id,
              username=current_user.username, resource_type="instance",
              details={"fields": changed}, ip=_ip(request))
    if budget_change is not None:
        audit(db, "ai.budget_changed", user_id=current_user.id, username=current_user.username,
              resource_type="instance",
              details={"limit": "instance", "old": budget_change[0], "new": budget_change[1]},
              ip=_ip(request))
    return ai_settings.status_payload(db, current_user)


# ── usage meter ──────────────────────────────────────────────────────────────

@router.get("/usage")
def get_usage(
    month: str | None = Query(default=None, description="YYYY-MM, default the current UTC month"),
    all_users: bool = Query(default=False, alias="all", description="Admins: totals across all users"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """The month's totals, plus ``limits``: ``{user_monthly_usd (own),
    admin_monthly_usd (an admin's cap), effective_monthly_usd (the lower),
    user_spent_usd, instance_monthly_usd, instance_spent_usd,
    remaining_usd}`` for the current UTC month (instance figures only for
    users whose calls can be charged to the instance key). With ``all``
    (admins), ``by_user`` rows and ``members`` (every member and admin)
    carry ``monthly_limit_usd`` (the user's own), ``admin_limit_usd`` (the
    cap an admin set, which the per-user input edits) and
    ``effective_limit_usd``."""
    if all_users and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        out = usage.month_summary(db, month=month, user_id=current_user.id, all_users=all_users)
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    out["limits"] = budget.limits_payload(db, current_user)
    if all_users:
        members = [u for u in db.query(User).order_by(User.username.asc()).all()
                   if is_member_or_above(u)]
        limits = {u.id: _limit_fields(u) for u in members}
        empty = {"monthly_limit_usd": None, "admin_limit_usd": None, "effective_limit_usd": None}
        for row in out.get("by_user", []):
            row.update(limits.get(row["user_id"], empty))
        out["members"] = [{"user_id": u.id, "username": u.username, **limits[u.id]} for u in members]
    return out


def _limit_fields(u: User) -> dict[str, float | None]:
    return {"monthly_limit_usd": u.ai_monthly_limit_usd, "admin_limit_usd": u.ai_admin_limit_usd,
            "effective_limit_usd": budget.effective_user_limit(u)}


class LimitBody(BaseModel):
    # USD per calendar month (UTC), all key sources. null removes the limit.
    user_monthly_usd: float | None = Field(default=None, ge=0.0, le=1_000_000.0)


def _set_user_limit(db: Session, target: User, value: float | None, actor: User,
                    request: Request, *, admin_cap: bool = False) -> None:
    """Set the user's own limit, or with ``admin_cap`` the cap an admin
    keeps on them (a separate column the user cannot change)."""
    old = target.ai_admin_limit_usd if admin_cap else target.ai_monthly_limit_usd
    if old == value:
        return
    if admin_cap:
        target.ai_admin_limit_usd = None if value is None else max(0.0, float(value))
    else:
        ai_settings.set_user_monthly_limit(target, value)
    db.commit()
    audit(db, "ai.budget_changed", user_id=actor.id, username=actor.username,
          resource_type="user", resource_id=target.id, resource_title=target.username,
          details={"limit": "admin" if admin_cap else "user", "user_id": target.id,
                   "old": old, "new": value},
          ip=_ip(request))


@router.put("/limits")
def set_own_limit(
    body: LimitBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Set (or with null, remove) the caller's own monthly AI spend limit.
    Returns ``{limits}`` as in GET /usage."""
    _set_user_limit(db, current_user, body.user_monthly_usd, current_user, request)
    return {"limits": budget.limits_payload(db, current_user)}


@router.put("/users/{user_id}/limit")
def set_user_limit(
    user_id: int,
    body: LimitBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Admins: set (or with null, remove) the cap kept on any user's
    monthly AI spend (``User.ai_admin_limit_usd``; the user's own limit is
    theirs, and the lower of the two applies). ``{user_id, username,
    monthly_limit_usd, admin_limit_usd, effective_limit_usd}``."""
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    _set_user_limit(db, target, body.user_monthly_usd, current_user, request, admin_cap=True)
    return {"user_id": target.id, "username": target.username, **_limit_fields(target)}


# ── activity: jobs and their calls ───────────────────────────────────────────

@router.get("/jobs")
def list_ai_jobs(
    days: int = Query(default=30, ge=1, le=366),
    user_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Running jobs, then the last ``days`` days of jobs grouped by book with
    spend (``backend/services/ai/jobs_view.py``). Members see their own;
    admins everyone's, or one user's with ``user_id``."""
    if is_admin(current_user):
        return jobs_view.list_jobs(db, user_id=user_id, days=days, with_usernames=True)
    if user_id is not None and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Admin access required")
    return jobs_view.list_jobs(db, user_id=current_user.id, days=days)


@router.get("/companion/overview")
def get_companion_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Settings > AI > Companion: one row per series (a standalone book is
    its own row) with coverage per volume, glossary counts, recap states,
    last merge, last job, this month's spend, the viewer's active job and
    ``anchor_book_id`` (``backend/services/ai/overview.py``). Members: the
    series they read or hold a position in; admins: every series with a
    digest, with ``readers``. Never calls a model."""
    from backend.services.ai import overview
    return overview.overview(db, current_user)


@router.get("/jobs/{job_id}/calls")
def list_ai_job_calls(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """The job's model calls with their tokens and price."""
    job = _visible_job(job_id, current_user, db)
    return {"job_id": job["id"], "calls": jobs_view.job_calls(db, job_id)}


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
    from backend.services import bindery_review  # auto-import skips files in review
    bindery_review.touch(seen)
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
    ai_assisted=true. When the proposal corrects the title or author, the
    metadata fetch runs again on the corrected identity (``refetched``,
    ``refetch_query``) and a matching edition supplies the fetch fields."""
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

def _visible_job(job_id: str, user: User, db: Session) -> dict[str, Any]:
    job = digest_job.get(job_id, db)
    if job is None or (job["user_id"] != user.id and not is_admin(user)):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/digest-jobs/{job_id}")
def get_digest_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Progress of a digest job: ``{id, status, scope, done, total, calls,
    cost_usd, est_seconds, est_cost_usd, current, ...}``. A job this
    process no longer holds is served from its ``ai_jobs`` row."""
    return _visible_job(job_id, current_user, db)


@router.post("/digest-jobs/{job_id}/cancel")
def cancel_digest_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Stop a job: a queued one is cancelled at once; a running one turns
    ``stopping`` at once, its units in flight finish and store their
    chapters, then ``cancelled``. The chapters already digested stay
    stored."""
    _visible_job(job_id, current_user, db)
    job = digest_job.cancel(job_id, db)
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
        "chapters": len(chapters), "coverage": None, "job": None, "last_job": None,
        "recap_cached": False, "glossary_count": 0, "previously": None, "scope": None,
        "series_recaps": False, "ask_cost_hint_usd": None,
    }
    if (not ai_settings.ai_enabled(db) or not ai_settings.feature_enabled(db, digest.FEATURE)
            or not (ai_settings.feature_enabled(db, ai_recap.FEATURE)
                    or ai_settings.feature_enabled(db, ai_recall.FEATURE))):
        out["reason"] = companion.FEATURE_OFF
        return out
    # The volume list (GET .../recap/volumes) needs no key and no position.
    out["series_recaps"] = (ai_settings.feature_enabled(db, ai_recap.FEATURE)
                            and volume_recap.has_series_recaps(db, current_user, book))
    out["job"] = digest_job.active_for(current_user.id, book.id)
    if out["job"] is None:
        # The latest ended job, so the page can offer "Continue" after a
        # stop, cancel or restart (it never queues on its own).
        latest = (db.query(AIJob.id)
                  .filter(AIJob.user_id == current_user.id, AIJob.book_id == book.id)
                  .order_by(AIJob.created_at.desc()).first())
        out["last_job"] = digest_job.get(latest[0], db) if latest else None
    pos = companion.reader_position(db, current_user, book)
    finished = bool(chapters) and companion.is_read(db, current_user, book)
    pos_k = (len(chapters) if finished else companion.position_chapter(book, pos.word_pct, db)) \
        if pos and chapters else None
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
    if feats["ask"]:
        from backend.services.ai import ask as ai_ask
        out["ask_cost_hint_usd"] = ai_ask.cost_hint_usd(db)
    out.update({
        "available": True,
        # ``finished``: the user marked the book read; the window is the
        # whole book (``chapter_idx`` = chapter count, no chapter title).
        "position": {
            "pct": pos.pct, "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title,
            "source": pos.source, "updated_at": _iso(pos.updated_at), "finished": window.finished,
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
    created_at, scope}``, or 202 ``{needs_digest: true, estimate, coverage,
    job, chapter_idx, chapter_title}`` when chapters before the position are
    not digested yet. The 202 queues nothing: start the digests with POST
    /books/{id}/digest, then ask again."""
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


# Recap as volumes: one recap per read earlier volume of the open book, from
# that volume's chapter summaries (backend/services/ai/volume_recap.py).

@router.get("/books/{book_id}/recap/volumes")
def get_recap_volumes(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """``{can_write, volumes: [{book_id, title, series_index, chapters,
    status: ready|partial|missing, digested, needed, text, created_at,
    missing_chapters}], current: {book_id, title, series_index, has_position,
    recap_cached}, write_all: {count, est_cost_usd, reading: {chapters, volumes,
    est_cost_usd, est_seconds}, job, last}}``. Read-only, never queues; works
    without a position. ``write_all``: what the "Write all" job (POST
    .../recap/volumes) would read first and write after, with conservative
    costs; ``job`` = the active job for this book, ``last`` = the newest
    finished "Write all" job (job snapshots as GET /digest-jobs/{id})."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    return volume_recap.volumes_payload(db, current_user, book)


@router.post("/books/{book_id}/recap/volumes", status_code=202)
def post_recap_volumes_all(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> JSONResponse:
    """"Write all": 202 ``{job}``, a digest job for this book (series scope,
    up to the position, or chapter 0 without one) whose steps are reading
    (the missing chapters of the read earlier volumes), merging (duplicate
    entries) and writing (every missing or stale volume recap, one call
    each). Poll GET /digest-jobs/{id}. 409 without a key, 409 ``budget``
    when the reading estimate plus the recaps exceed what is left of a
    monthly limit, 409 ``already_running`` while a job for this book is
    active. The body is ignored (send ``{}``)."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    return JSONResponse(status_code=202, content=volume_recap.write_all(db, current_user, book))


@router.post("/books/{book_id}/reconcile", status_code=202)
def post_reconcile(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> JSONResponse:
    """"Merge duplicates": 202 ``{job}``, a digest job that reads nothing and
    runs the merging step (duplicate glossary entries of the book's series)
    only. 409 without a key, 409 ``already_running`` while a job for this
    book is active."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    return JSONResponse(status_code=202, content=volume_recap.start_reconcile(db, current_user, book))


def _start_window(db: Session, user: User, book: Book) -> companion.Window:
    try:
        return companion.start_window(db, user, book)
    except companion.WindowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


class VolumeRecapBody(BaseModel):
    # Write from the summaries that exist even when chapters are undigested.
    force: bool = False
    # "Ask again": clear the volume's failed placeholders and rewrite its
    # own-scope row in place (a call even when nothing new was digested).
    again: bool = False


def _volume_of(db: Session, user: User, book: Book, vol_id: int) -> Book:
    vol = volume_recap.find_volume(db, user, book, vol_id)
    if vol is None:
        raise HTTPException(status_code=404, detail="Not a read earlier volume of this book")
    return vol


@router.post("/books/{book_id}/recap/volumes/{vol_id}")
def post_recap_volume(
    book_id: int,
    vol_id: int,
    body: VolumeRecapBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> Any:
    """200 with the volume item (written now, or the row when nothing was
    digested since it was written), or 202 ``{needs_digest: true, estimate,
    coverage}`` when the volume has undigested chapters and not ``force``.
    Nothing is queued: start the digest with POST /books/{vol_id}/digest
    {scope: "book"}. ``again`` is "Ask again" (clears failed placeholders,
    rewrites in place). 409 ``already_running`` while one is in flight."""
    body = body or VolumeRecapBody()
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    vol = _volume_of(db, current_user, book, vol_id)
    status, payload = volume_recap.request_volume(db, current_user, vol, force=body.force,
                                                  again=body.again)
    return JSONResponse(status_code=status, content=payload)


@router.delete("/books/{book_id}/recap/volumes/{vol_id}")
def delete_recap_volume(
    book_id: int,
    vol_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """Delete the volume's own-scope recap: ``{deleted}``. 409 without a
    key. The user-scope row of the finished-book hook stays. "Ask again" is
    POST with ``{again: true}``."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_recap.FEATURE)
    vol = _volume_of(db, current_user, book, vol_id)
    return {"deleted": volume_recap.clear_volume(db, current_user, vol)}


@router.get("/books/{book_id}/glossary")
def get_glossary(
    book_id: int,
    request: Request,
    chapter: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> Any:
    """The glossary up to the position chapter (from storage, no model call).
    Read-only: never queues work; ``job`` is the active digest job for the
    book, if any. Digests are started with POST /books/{id}/digest."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    window = _web_window(db, current_user, book, chapter)
    return companion.json_response(companion.glossary_payload(db, current_user, book, window),
                                   request.headers.get("accept-encoding"))


@router.get("/books/{book_id}/glossary/entry")
def get_glossary_entry(
    book_id: int,
    entity_id: int | None = Query(default=None),
    name: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None, max_length=20),
    chapter: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """``{entry}``: one glossary entry of the window in the full v2 shape
    (facts and timeline places with ``chapter_title``, the timeline's last
    200 lines), by ``entity_id`` or by ``name`` (+ ``kind``). 404 when the
    window has no such entry."""
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    if entity_id is None and not (name or "").strip():
        raise HTTPException(status_code=422, detail="Give an entity_id or a name.")
    window = _web_window(db, current_user, book, chapter)
    entry = companion.glossary_entry(db, current_user, book, window, entity_id=entity_id,
                                     name=name, kind=kind)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such entry up to your position")
    return {"entry": entry}


@router.get("/books/{book_id}/digest/estimate")
def get_digest_estimate(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """What reading the missing chapters before the position would cost and
    take: ``{book: {...}, series: {...} | null, chapter_idx,
    chapter_title}``, each scope ``{scope, chapters, calls, words,
    est_seconds, est_cost_usd, model, remaining_budget_usd, over_budget,
    volumes: [{book_id, title, chapters}]}``. Never calls a model. Without
    a position: the read earlier volumes only (chapter 0 of this book)."""
    book = _visible_book(db, current_user, book_id)
    window = _start_window(db, current_user, book)
    return companion.digest_estimates(db, current_user, book, window)


class DigestBody(BaseModel):
    # "book": this book's chapters before the position; "series": also every
    # chapter of the earlier volumes the user marked read.
    scope: Literal["book", "series"] = "book"


@router.post("/books/{book_id}/digest")
def post_digest(
    book_id: int,
    body: DigestBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> Any:
    """Start digesting the chapters before the position: 202 ``{job,
    estimate, coverage, scope, chapter_idx, chapter_title}``, or 200 with
    ``job`` null (or the active job) when nothing is missing. 409 without a
    key, 409 ``reason: "budget"`` when the estimate is over what is left of
    a monthly spend limit. Without a position the window is chapter 0:
    nothing of this book, the read earlier volumes in full."""
    body = body or DigestBody()
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    window = _start_window(db, current_user, book)
    status, payload = companion.start_digest(db, current_user, book, window, body.scope)
    return JSONResponse(status_code=status, content=payload)


@router.get("/books/{book_id}/reread/estimate")
def get_reread_estimate(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """What Read again would cost and delete (admin only, 403 otherwise):
    ``{book, series, chapter_idx, chapter_title, deletes: {book: {digests,
    recaps}, series: {volumes, digests, recaps, entities}}}``; the scopes in
    the /digest/estimate shape, counting every chapter of the scope up to the
    position (all of them will be missing); ``deletes.series`` counts every
    volume of the series, not only the read ones. Never calls a model."""
    book = _visible_book(db, current_user, book_id)
    window = _start_window(db, current_user, book)
    return reread.estimate(db, current_user, book, window)


@router.post("/books/{book_id}/reread")
def post_reread(
    book_id: int,
    body: DigestBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> Any:
    """Read again (admin only, 403 otherwise): delete the stored notes of
    the scope (``book``: this book's digests and recaps; ``series``: the
    digests and recaps of every volume of the series and every entity,
    entity note and merge verdict of it) and start reading it: 202 like
    POST /digest. 409 ``already_running`` while a job reads the book or
    series, 409 ``reason: "budget"`` over a monthly spend limit, 409
    ``nothing_to_read`` (also a series re-read with no read earlier
    volume); nothing is deleted on any of these. A start that fails after
    the delete keeps its status and says the notes are gone. Audited as
    ``ai.reread``."""
    body = body or DigestBody()
    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, digest.FEATURE)
    window = _start_window(db, current_user, book)
    status, payload = reread.run(db, current_user, book, window, body.scope)
    return JSONResponse(status_code=status, content=payload)


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


@router.post("/books/{book_id}/ask")
def post_ask(
    book_id: int,
    # ``{question}``, validated in the route: a bad body is a 422 whose
    # ``detail`` is a plain message, never pydantic's error list.
    body: Any = Depends(companion.loose_json_body),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_user),
) -> dict[str, Any]:
    """``{answer, found, quotes, chapters_read, chapter_idx, chapter_title}``:
    a question answered from the text before the reader's position."""
    from backend.services.ai import ask as ai_ask

    book = _visible_book(db, current_user, book_id)
    ai.check_feature_available(db, ai_ask.FEATURE)
    ai.check_feature_available(db, digest.FEATURE)
    question = body.get("question") if isinstance(body, dict) else None
    question = question if isinstance(question, str) else ""
    if not question.strip() or len(question) > ai_ask.MAX_QUESTION_CHARS:
        raise HTTPException(status_code=422,
                            detail=f"Type a question of up to {ai_ask.MAX_QUESTION_CHARS} characters.")
    window = _web_window(db, current_user, book, None)
    try:
        return ai_ask.ask(db, current_user, book, window, question)
    except ai_ask.BadQuestion as exc:
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
    content: dict[str, Any] = {"detail": exc.message}
    if exc.reason:
        content["reason"] = exc.reason
    return JSONResponse(status_code=exc.status_code, content=content)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AIError, _ai_error_handler)
