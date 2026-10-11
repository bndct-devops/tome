"""Background digest jobs: one serial worker, in memory, single process.

Modelled on the bake / word-count jobs: module-global state behind a lock, a
cancel event per job, one daemon worker thread that survives the request that
queued the work. A job is "digest ``book`` up to (not including) chapter
``upto`` for ``user``'s scope": at start it expands to the (volume, chapter)
pairs of that scope that have no digest yet, in reading order (the user's
read earlier volumes, then the book), and processes them one call at a time.

- Every model call goes through ``ai.run_feature`` (feature
  ``reading_digest``), so each call is one usage row billed to the user who
  triggered the job, under their key source.
- A failing chapter (provider error, refusal, unreadable answer) is recorded
  in ``errors`` and skipped; the job continues. Three failures in a row stop
  it (a rejected key or an outage would otherwise burn through the book).
- ``AINotConfigured`` / ``AIDisabled`` (key removed, feature switched off)
  stop the job at once with ``stop_reason``.
- Enqueues coalesce per (user, book): a request whose ``upto`` is covered by
  a queued or running job returns that job; a larger one extends a queued
  job in place. Follow-ups merge into the existing job. A
  ``current_book_only`` job (the position trigger) digests only the book's
  own chapters, not the read earlier volumes; merging a full request into it
  widens it.
- A billed answer that cannot be used (refusal, cut off) is stored by
  ``digest.digest_chapter`` as a placeholder, so no later job re-sends it.
- Follow-ups run after the digests, in order, when the job was not cancelled
  or stopped: ``register_followup(name, fn(db, user, book))``. The recap
  module registers ``previously_recap``; without a handler the follow-up is
  reported as skipped, and so is one whose handler returns a message (str)
  saying why it did nothing.

A restart drops the queue; digests are stored per chapter, so a re-enqueue
resumes where the last run stopped.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.book import Book
from backend.models.user import User
from backend.services.ai.provider import AIDisabled, AIError, AINotConfigured

log = logging.getLogger(__name__)

FOLLOWUP_PREVIOUSLY = "previously_recap"

MAX_CONSECUTIVE_FAILURES = 3
_MAX_ERRORS = 50
_MAX_FINISHED_KEPT = 100

# status: queued | running | done | cancelled | stopped | error
ACTIVE = ("queued", "running")

FollowupFn = Callable[[Session, User, Book], Any]

_lock = threading.Lock()
_queue: deque["Job"] = deque()
_jobs: dict[str, "Job"] = {}
_thread: Optional[threading.Thread] = None
_followups: dict[str, FollowupFn] = {}

# Indirection for tests: the session factory and how the worker is started.
_session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal


def _spawn_worker() -> threading.Thread:
    t = threading.Thread(target=run_pending, name="ai-digest", daemon=True)
    t.start()
    return t


@dataclass
class Job:
    id: str
    user_id: int
    book_id: int
    upto: int
    followups: list[str] = field(default_factory=list)
    current_book_only: bool = False
    status: str = "queued"
    done: int = 0
    total: int = 0
    failed: int = 0
    current: Optional[dict[str, Any]] = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: Optional[str] = None
    followup_results: list[dict[str, Any]] = field(default_factory=list)
    followups_started: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        def iso(ts: Optional[float]) -> Optional[str]:
            return datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else None

        return {
            "id": self.id,
            "kind": "digest",
            "status": self.status,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "upto_chapter": self.upto,
            "done": self.done,
            "total": self.total,
            "failed": self.failed,
            "current": dict(self.current) if self.current else None,
            "errors": [dict(e) for e in self.errors],
            "stop_reason": self.stop_reason,
            "followups": [
                *(dict(r) for r in self.followup_results),
                *({"name": n, "status": "pending"} for n in self.followups[len(self.followup_results):]),
            ],
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
        }


# ── public API ───────────────────────────────────────────────────────────────

def register_followup(name: str, fn: FollowupFn) -> None:
    """Register what a follow-up name does. ``fn(db, user, book)`` runs in the
    worker thread with its own session after the job's digests."""
    _followups[name] = fn


def enqueue(*, user_id: int, book_id: int, upto: int,
            followups: tuple[str, ...] | list[str] = (),
            current_book_only: bool = False) -> dict[str, Any]:
    """Queue (or coalesce into) a digest job; returns its snapshot."""
    global _thread
    with _lock:
        for job in _jobs.values():
            if job.user_id != user_id or job.book_id != book_id or job.status not in ACTIVE:
                continue
            can_add_followups = job.status == "queued" or not job.followups_started
            new_followups = [f for f in followups if f not in job.followups]
            if job.status == "queued":
                job.upto = max(job.upto, upto)
                job.current_book_only = job.current_book_only and current_book_only
            elif upto > job.upto or (job.current_book_only and not current_book_only):
                continue  # running and smaller: a new job picks up the rest
            if new_followups and not can_add_followups:
                continue
            job.followups.extend(new_followups)
            return job.snapshot()

        job = Job(id=uuid.uuid4().hex[:12], user_id=user_id, book_id=book_id, upto=upto,
                  followups=list(dict.fromkeys(followups)), current_book_only=current_book_only)
        _jobs[job.id] = job
        _queue.append(job)
        _prune_locked()
        if _thread is None or not _thread.is_alive():
            _thread = _spawn_worker()
        return job.snapshot()


def get(job_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return job.snapshot() if job else None


def active_for(user_id: int, book_id: int) -> Optional[dict[str, Any]]:
    """The queued or running job for (user, book), if any."""
    with _lock:
        for job in _jobs.values():
            if job.user_id == user_id and job.book_id == book_id and job.status in ACTIVE:
                return job.snapshot()
        return None


def cancel(job_id: str) -> Optional[dict[str, Any]]:
    """Cancel a queued job at once, a running one before its next chapter."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if job.status == "queued":
            try:
                _queue.remove(job)
            except ValueError:
                pass
            job.status = "cancelled"
            job.finished_at = time.time()
        elif job.status == "running":
            job.cancel.set()
        return job.snapshot()


def reset() -> None:
    """Tests only: forget every job (a running worker finishes its chapter)."""
    global _thread
    with _lock:
        for job in _jobs.values():
            job.cancel.set()
        _queue.clear()
        _jobs.clear()
        _thread = None


def run_pending() -> None:
    """Process queued jobs until the queue is empty. The worker thread's
    body; tests call it directly to run jobs in the calling thread."""
    global _thread
    while True:
        with _lock:
            if not _queue:
                if _thread is threading.current_thread():
                    _thread = None
                return
            job = _queue.popleft()
            job.status = "running"
            job.started_at = time.time()
        try:
            _run(job)
        except Exception as exc:  # noqa: BLE001 - a crashed job must not kill the worker
            log.exception("digest job %s crashed", job.id)
            _finish(job, "error", str(exc) or type(exc).__name__)


# ── internals ────────────────────────────────────────────────────────────────

def _prune_locked() -> None:
    finished = [j for j in _jobs.values() if j.status not in ACTIVE]
    if len(finished) <= _MAX_FINISHED_KEPT:
        return
    for j in sorted(finished, key=lambda j: j.finished_at or 0)[: len(finished) - _MAX_FINISHED_KEPT]:
        _jobs.pop(j.id, None)


def _finish(job: Job, status: str, stop_reason: Optional[str] = None) -> None:
    with _lock:
        job.status = status
        job.current = None
        job.finished_at = time.time()
        if stop_reason:
            job.stop_reason = stop_reason


def _record_error(job: Job, book_id: int, idx: int, message: str) -> None:
    with _lock:
        job.failed += 1
        if len(job.errors) < _MAX_ERRORS:
            job.errors.append({"book_id": book_id, "chapter_idx": idx, "message": message})


def _run(job: Job) -> None:
    from backend.services.ai import digest

    with _session_factory() as db:
        user = db.get(User, job.user_id)
        book = db.get(Book, job.book_id)
        if user is None or book is None:
            _finish(job, "error", "The book or the user no longer exists.")
            return
        scope = digest.Scope(book=book) if job.current_book_only else digest.user_scope(db, user, book)
        work = [(b.id, c.idx, c.title, b.title) for b, c in digest.missing_chapters(db, scope, job.upto)]
        with _lock:
            job.total = len(work)

        consecutive = 0
        for book_id, idx, chapter_title, book_title in work:
            if job.cancel.is_set():
                _finish(job, "cancelled")
                return
            with _lock:
                job.current = {"book_id": book_id, "title": book_title,
                               "chapter_idx": idx, "chapter_title": chapter_title}
            try:
                vol = db.get(Book, book_id)
                if vol is None:
                    raise digest.DigestUnavailable("The book no longer exists.")
                digest.digest_chapter(db, user, vol, idx)
                consecutive = 0
            except (AINotConfigured, AIDisabled) as exc:
                db.rollback()
                _finish(job, "stopped", exc.message)
                return
            except AIError as exc:
                db.rollback()
                _record_error(job, book_id, idx, exc.message)
                consecutive += 1
            except Exception as exc:  # noqa: BLE001 - one bad chapter never aborts the run
                db.rollback()
                log.warning("digest job %s: chapter %s of book %s failed (%s)",
                            job.id, idx, book_id, type(exc).__name__)
                _record_error(job, book_id, idx, str(exc) or type(exc).__name__)
                consecutive += 1
            with _lock:
                job.done += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                _finish(job, "stopped", "Stopped after repeated failures. Try again later.")
                return

        if job.cancel.is_set():
            _finish(job, "cancelled")
            return

        with _lock:
            job.current = None
            job.followups_started = True
            followups = list(job.followups)
        for name in followups:
            fn = _followups.get(name)
            if fn is None:
                result = {"name": name, "status": "skipped", "message": "Not available yet."}
            else:
                try:
                    skipped = fn(db, user, book)
                    result = ({"name": name, "status": "skipped", "message": skipped}
                              if isinstance(skipped, str) else {"name": name, "status": "done"})
                except (AINotConfigured, AIDisabled, AIError) as exc:
                    db.rollback()
                    result = {"name": name, "status": "failed", "message": exc.message}
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    log.exception("digest job %s: follow-up %s failed", job.id, name)
                    result = {"name": name, "status": "failed", "message": type(exc).__name__}
            with _lock:
                job.followup_results.append(result)
        _finish(job, "done")
