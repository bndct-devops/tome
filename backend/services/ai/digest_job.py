"""Background digest jobs: a pool of worker threads, mirrored to ``ai_jobs``.

A job is "digest ``book`` up to (not including) chapter ``upto`` for
``user``'s scope". When a worker starts it, it expands into units
(``digest.plan_units``: the missing chapters in reading order, grouped into
consecutive chapters of one volume) and the units go onto the shared work
list. Progress counts chapters (``done`` includes failed ones).

Execution (in memory, single process, like the bake and word-count jobs):

- ``WORKERS`` daemon threads, started on demand and exiting when there is
  nothing left for them. Workers take units from any job: oldest job first,
  its units in order. The units of one volume never run at the same time,
  across all jobs: each unit builds on the glossary of the units before it
  in that volume (and its cached prefix), and two jobs never read the same
  chapter twice at once. Units of different volumes run in parallel, so a
  backlog of finished volumes goes four at a time.
- Every model call goes through ``ai.run_feature`` (feature
  ``reading_digest``), one usage row per call, billed to the user who
  started the job, carrying the job id and the volume's book id.
- A multi-chapter unit whose answer Tome cannot use (refusal, cut off,
  chapter numbers it cannot place) stores nothing and goes back to the front
  of the job's units as one unit per chapter (``digest.UnitSplit``); that
  counts as a call, not a failure.
- A failing unit (provider error, a single chapter's refusal) is recorded in
  ``errors`` (one per chapter) and the job goes on. A chapter the model left
  out of a multi-chapter answer is recorded the same way and stays
  undigested. Three failed units in a row stop the job.
- ``AINotConfigured`` / ``AIDisabled`` (key removed, feature off) and
  ``AIBudgetExceeded`` (a monthly limit) stop the job at once with
  ``stop_reason``: units not started are dropped, units in flight finish
  (status ``stopping`` meanwhile), then status ``stopped``.
- ``cancel`` sets ``stopping`` at once (visible to polls), drops the units
  not started, lets the ones in flight finish and store their chapters,
  then ``cancelled``. A queued job is cancelled at once.
- Enqueues coalesce per (user, book): a request whose ``upto`` is covered by
  a queued or running job returns that job; a larger one extends a queued
  job in place. A ``current_book_only`` job (scope "book") digests only the
  book's own chapters; merging a series request into a queued one widens it.
- Follow-ups run after the last unit, on one worker, unless the job was
  cancelled or stopped: ``register_followup(name, fn(db, user, book))``.
  The recap module registers ``previously_recap``; without a handler the
  follow-up is reported as skipped, and so is one whose handler returns a
  message (str) saying why it did nothing. Their calls carry the job id.

Persistence: every state change (enqueue, start, each unit, stop, finish) is
mirrored to the job's ``ai_jobs`` row (``backend/models/ai_job.py``), with
``calls`` and ``cost_usd`` summed from its usage rows and the estimate made
at expansion. On startup ``mark_interrupted`` closes the rows a restart left
live; nothing resumes on its own (digests are stored per chapter, so a new
job continues where the last one stopped). ``last_cancelled`` reads the
rows: the automatic triggers stay away from a book whose latest job the user
cancelled until the user starts one again.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time
import uuid
from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.ai_job import LIVE_STATUSES, AIJob
from backend.models.ai_usage import AIUsage
from backend.models.book import Book, BookChapter
from backend.models.user import User
from backend.services.ai.provider import AIBudgetExceeded, AIDisabled, AIError, AINotConfigured

log = logging.getLogger(__name__)

WORKERS = 4
FOLLOWUP_PREVIOUSLY = "previously_recap"

MAX_CONSECUTIVE_FAILURES = 3
_MAX_ERRORS = 50
_MAX_FINISHED_KEPT = 100

LEFT_OUT = "The answer left this chapter out. It is read again next time."
REPEATED_FAILURES = "Stopped after repeated failures. Try again later."
INTERRUPTED = "The server restarted before this job finished."

# status: queued | running | stopping | done | cancelled | stopped | error
# (+ interrupted, rows only: set at startup)
ACTIVE = ("queued", "running", "stopping")   # the book's current job
_JOINABLE = ("queued", "running")            # a new request may coalesce into it

FollowupFn = Callable[[Session, User, Book], Any]

_lock = threading.Lock()
_queue: deque["Job"] = deque()               # queued jobs, not expanded yet
_jobs: dict[str, "Job"] = {}
_threads: set[threading.Thread] = set()
_working = 0                                 # workers holding an item
_busy_books: set[int] = set()                # volumes with a unit in flight
_followups: dict[str, FollowupFn] = {}
_seq = itertools.count()
_tokens = itertools.count()
_last_created = 0.0

# Indirection for tests: the session factory and how a worker is started
# (tests replace it with ``lambda: None`` and call ``run_pending``).
_session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal


def _start_thread() -> threading.Thread:
    t = threading.Thread(target=run_pending, name="ai-digest", daemon=True)
    t.start()
    return t


_spawn_worker: Callable[[], Optional[threading.Thread]] = _start_thread


def _utc(ts: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None) if ts else None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _next_created() -> float:
    """Creation times strictly increase, so "the latest job" is never a tie."""
    global _last_created
    _last_created = max(time.time(), _last_created + 1e-6)
    return _last_created


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
    calls: int = 0
    cost_usd: float = 0.0
    est_seconds: Optional[float] = None
    est_cost_usd: Optional[float] = None
    est_chapters: Optional[int] = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: Optional[str] = None
    followup_results: list[dict[str, Any]] = field(default_factory=list)
    followups_started: bool = False
    created_at: float = field(default_factory=_next_created)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    seq: int = field(default_factory=lambda: next(_seq))   # creation order
    # pool state
    units: deque = field(default_factory=deque)             # digest.Unit, not started
    in_flight: dict[int, dict[str, Any]] = field(default_factory=dict)
    expanding: bool = False
    final: Optional[tuple[str, Optional[str]]] = None       # a stop waiting for in-flight units
    consecutive: int = 0
    version: int = 0
    mirrored: int = -1
    mirror_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def scope(self) -> str:
        return "book" if self.current_book_only else "series"

    @property
    def current(self) -> Optional[dict[str, Any]]:
        if not self.in_flight:
            return None
        return dict(self.in_flight[min(self.in_flight)])

    @property
    def finished(self) -> bool:
        return self.status not in ACTIVE

    def followups_payload(self) -> list[dict[str, Any]]:
        return [
            *(dict(r) for r in self.followup_results),
            *({"name": n, "status": "pending"} for n in self.followups[len(self.followup_results):]),
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": "digest",
            "status": self.status,
            "scope": self.scope,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "upto_chapter": self.upto,
            "done": self.done,
            "total": self.total,
            "failed": self.failed,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "est_seconds": self.est_seconds,
            "est_cost_usd": self.est_cost_usd,
            "est_chapters": self.est_chapters,
            "current": self.current,
            "in_flight": len(self.in_flight),
            "errors": [dict(e) for e in self.errors],
            "stop_reason": self.stop_reason,
            "followups": self.followups_payload(),
            "created_at": _iso(_utc(self.created_at)),
            "started_at": _iso(_utc(self.started_at)),
            "finished_at": _iso(_utc(self.finished_at)),
        }

    def row_fields(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id, "book_id": self.book_id, "kind": "digest",
            "scope": self.scope, "status": self.status, "upto_chapter": self.upto,
            "total": self.total, "done": self.done, "failed": self.failed,
            "calls": self.calls, "cost_usd": round(self.cost_usd, 6),
            "est_seconds": self.est_seconds, "est_cost_usd": self.est_cost_usd,
            "est_chapters": self.est_chapters, "stop_reason": self.stop_reason,
            "followups_json": json.dumps(self.followups_payload()),
            "created_at": _utc(self.created_at), "started_at": _utc(self.started_at),
            "finished_at": _utc(self.finished_at),
        }


def row_snapshot(row: AIJob) -> dict[str, Any]:
    """A job snapshot built from its ``ai_jobs`` row (a job this process no
    longer holds: finished long ago, or from before a restart). A row still
    marked live is reported ``interrupted``: only this process runs jobs,
    and it does not hold this one."""
    try:
        followups = json.loads(row.followups_json or "[]")
    except ValueError:
        followups = []
    status = "interrupted" if row.status in LIVE_STATUSES else row.status
    return {
        "id": row.id, "kind": row.kind, "status": status, "scope": row.scope,
        "user_id": row.user_id, "book_id": row.book_id, "upto_chapter": row.upto_chapter,
        "done": row.done, "total": row.total, "failed": row.failed,
        "calls": row.calls, "cost_usd": round(row.cost_usd or 0.0, 6),
        "est_seconds": row.est_seconds, "est_cost_usd": row.est_cost_usd,
        "est_chapters": row.est_chapters, "current": None, "in_flight": 0, "errors": [],
        "stop_reason": row.stop_reason, "followups": followups,
        "created_at": _iso(row.created_at), "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


# ── public API ───────────────────────────────────────────────────────────────

def register_followup(name: str, fn: FollowupFn) -> None:
    """Register what a follow-up name does. ``fn(db, user, book)`` runs in a
    worker thread with its own session after the job's digests."""
    _followups[name] = fn


def enqueue(*, user_id: int, book_id: int, upto: int,
            followups: tuple[str, ...] | list[str] = (),
            current_book_only: bool = False) -> dict[str, Any]:
    """Queue (or coalesce into) a digest job; returns its snapshot."""
    target: Optional[Job] = None
    with _lock:
        for job in _jobs.values():
            if (job.user_id != user_id or job.book_id != book_id or job.status not in _JOINABLE
                    or job.cancel.is_set() or job.final is not None):
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
            job.version += 1
            target = job
            break
        if target is None:
            target = Job(id=uuid.uuid4().hex[:12], user_id=user_id, book_id=book_id, upto=upto,
                         followups=list(dict.fromkeys(followups)),
                         current_book_only=current_book_only)
            _jobs[target.id] = target
            _queue.append(target)
            _prune_locked()
            _ensure_workers_locked()
    _mirror(target)
    with _lock:
        return target.snapshot()


def get(job_id: str, db: Optional[Session] = None) -> Optional[dict[str, Any]]:
    """The job's live snapshot, else (with ``db``) its row's."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            return job.snapshot()
    if db is not None:
        row = db.get(AIJob, job_id)
        if row is not None:
            return row_snapshot(row)
    return None


def active_for(user_id: int, book_id: int) -> Optional[dict[str, Any]]:
    """The queued, running or stopping job for (user, book), if any (the
    newest when there are several)."""
    with _lock:
        live = [j for j in _jobs.values()
                if j.user_id == user_id and j.book_id == book_id and j.status in ACTIVE]
        return max(live, key=lambda j: j.seq).snapshot() if live else None


def running(user_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Snapshots of every live job (queued, running, stopping), oldest first;
    one user's when ``user_id`` is given."""
    with _lock:
        live = [j for j in _jobs.values()
                if j.status in ACTIVE and (user_id is None or j.user_id == user_id)]
        return [j.snapshot() for j in sorted(live, key=lambda j: j.seq)]


def last_cancelled(db: Session, user_id: int, book_id: int) -> bool:
    """The latest job for (user, book) was cancelled by the user (or is being
    cancelled), per its ``ai_jobs`` row. The automatic triggers do not
    enqueue for that book again until the user starts a job themselves
    (which becomes the latest). A job stopped by Tome (budget, key,
    failures) does not count."""
    row = (
        db.query(AIJob.status, AIJob.stop_reason)
        .filter(AIJob.user_id == user_id, AIJob.book_id == book_id)
        .order_by(AIJob.created_at.desc())
        .first()
    )
    if row is None:
        return False
    status, reason = row
    return status == "cancelled" or (status == "stopping" and reason is None)


def cancel(job_id: str, db: Optional[Session] = None) -> Optional[dict[str, Any]]:
    """Cancel a job. Queued: cancelled at once. Running: ``stopping`` at
    once, the units not started are dropped, the ones in flight finish and
    store their chapters, then ``cancelled``. A finished job (or one only
    known from its row, with ``db``) is returned unchanged."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            if job.status == "queued":
                try:
                    _queue.remove(job)
                except ValueError:
                    pass
                job.cancel.set()
                _finalize_locked(job, "cancelled")
            elif job.status in ("running", "stopping") and not job.cancel.is_set():
                job.cancel.set()
                job.units.clear()
                if job.in_flight or job.expanding or job.followups_started:
                    job.status = "stopping"
                    job.version += 1
                else:
                    _finalize_locked(job, job.final[0] if job.final else "cancelled")
    if job is None:
        return get(job_id, db)
    _mirror(job)
    with _lock:
        return job.snapshot()


def mark_interrupted(db: Session) -> int:
    """Startup: rows a previous process left queued, running or stopping are
    over; mark them ``interrupted``. Never resumed on their own. A row left
    ``stopping`` without a ``stop_reason`` was a user's cancel in progress:
    it becomes ``cancelled``, so ``last_cancelled`` still holds the triggers
    back."""
    now = datetime.utcnow()
    rows = db.query(AIJob).filter(AIJob.status.in_(LIVE_STATUSES)).all()
    for row in rows:
        row.finished_at = now
        if row.status == "stopping" and not row.stop_reason:
            row.status = "cancelled"
            continue
        row.status = "interrupted"
        if not row.stop_reason:
            row.stop_reason = INTERRUPTED
    db.commit()
    return len(rows)


def reset() -> None:
    """Tests only: forget every job (a running worker finishes its unit)."""
    global _working
    with _lock:
        for job in _jobs.values():
            job.cancel.set()
            job.units.clear()
        _queue.clear()
        _jobs.clear()
        _threads.clear()
        _busy_books.clear()
        _working = 0


def run_pending() -> None:
    """Take work until there is none left for this thread: the body of each
    worker thread; tests call it directly to run every queued job, in order,
    in the calling thread."""
    global _working
    while True:
        with _lock:
            item = _take_locked()
            if item is None:
                _threads.discard(threading.current_thread())
                return
            _working += 1
        kind, job, unit, token = item
        try:
            if kind == "expand":
                _expand(job)
            else:
                _run_unit(job, unit, token)
        except Exception as exc:  # noqa: BLE001 - a crashed job must not kill the worker
            log.exception("digest job %s crashed", job.id)
            with _lock:
                if unit is not None:
                    _busy_books.discard(unit.book_id)
                    job.in_flight.pop(token, None)
                job.expanding = False
                job.units.clear()
                if not job.finished and not job.in_flight:
                    _finalize_locked(job, "error", str(exc) or type(exc).__name__)
                elif not job.finished:
                    job.final = ("error", str(exc) or type(exc).__name__)
                    job.status = "stopping"
                    job.version += 1
            _mirror(job)
        finally:
            with _lock:
                _working -= 1


# ── internals ────────────────────────────────────────────────────────────────

def _prune_locked() -> None:
    finished = [j for j in _jobs.values() if j.finished]
    if len(finished) <= _MAX_FINISHED_KEPT:
        return
    for j in sorted(finished, key=lambda j: j.finished_at or 0)[: len(finished) - _MAX_FINISHED_KEPT]:
        _jobs.pop(j.id, None)


def _runnable_locked() -> list[Job]:
    return sorted((j for j in _jobs.values()
                   if j.status == "running" and j.units and not j.cancel.is_set() and j.final is None),
                  key=lambda j: j.seq)


def _take_locked() -> Optional[tuple[str, Job, Any, Optional[int]]]:
    """The next item for a worker: a unit (oldest job first, its units in
    order, on a volume no other worker is reading), else a queued job to
    expand, else None."""
    for job in _runnable_locked():
        for i, unit in enumerate(job.units):
            if unit.book_id in _busy_books:
                continue
            del job.units[i]
            _busy_books.add(unit.book_id)
            token = next(_tokens)
            job.in_flight[token] = {"book_id": unit.book_id, "title": None,
                                    "chapter_idx": unit.idxs[0], "chapter_title": None,
                                    "chapters": len(unit.idxs)}
            job.version += 1
            return "unit", job, unit, token
    if _queue:
        job = _queue.popleft()
        job.status = "running"
        job.started_at = time.time()
        job.expanding = True
        job.version += 1
        return "expand", job, None, None
    return None


def _ensure_workers_locked() -> None:
    """Start workers (up to ``WORKERS``) for the work nobody has taken yet."""
    for t in [t for t in _threads if not t.is_alive()]:
        _threads.discard(t)
    free_volumes = {u.book_id for j in _runnable_locked() for u in j.units} - _busy_books
    available = len(_queue) + len(free_volumes)
    idle = len(_threads) - _working
    while len(_threads) < WORKERS and idle < available:
        t = _spawn_worker()
        if t is None:
            return
        _threads.add(t)
        idle += 1


def _finalize_locked(job: Job, status: str, stop_reason: Optional[str] = None) -> None:
    job.status = status
    job.units.clear()
    job.in_flight.clear()
    job.expanding = False
    job.finished_at = time.time()
    if stop_reason:
        job.stop_reason = stop_reason
    job.version += 1


def _halt_locked(job: Job, status: str, reason: str) -> None:
    """Stop a running job for ``reason``: drop the units not started; the
    ones in flight finish first (status ``stopping`` meanwhile)."""
    if job.final is None:
        job.final = (status, reason)
        job.stop_reason = reason
    job.units.clear()
    if not job.finished:
        job.status = "stopping"
    job.version += 1


def _settle_locked(job: Job) -> bool:
    """After a unit: finish the job when nothing is left in flight or to do.
    True when the follow-ups should run now (this worker runs them)."""
    if job.finished or job.units or job.in_flight or job.expanding:
        return False
    if job.final is not None:
        _finalize_locked(job, *job.final)
        return False
    if job.cancel.is_set():
        _finalize_locked(job, "cancelled")
        return False
    job.followups_started = True
    job.version += 1
    return True


def _record_error_locked(job: Job, book_id: int, idx: int, message: str) -> None:
    job.failed += 1
    if len(job.errors) < _MAX_ERRORS:
        job.errors.append({"book_id": book_id, "chapter_idx": idx, "message": message})


def _job_spend(db: Session, job_id: str) -> tuple[int, float]:
    calls, cost = db.query(func.count(AIUsage.id), func.sum(AIUsage.cost_usd)).filter(
        AIUsage.job_id == job_id).one()
    return int(calls or 0), float(cost or 0.0)


def _mirror(job: Job) -> None:
    """Write the job's state to its ``ai_jobs`` row. Never raises; a stale
    snapshot (another worker wrote a newer one) is not written."""
    with _lock:
        fields = job.row_fields()
        version = job.version
    with job.mirror_lock:
        if version <= job.mirrored:
            return
        try:
            with _session_factory() as db:
                row = db.get(AIJob, job.id)
                if row is None:
                    row = AIJob(id=job.id)
                    db.add(row)
                for name, value in fields.items():
                    setattr(row, name, value)
                db.commit()
            job.mirrored = version
        except Exception:  # noqa: BLE001 - the job runs on; the next change writes again
            log.exception("could not write ai_jobs row for job %s", job.id)


def _expand(job: Job) -> None:
    """Plan the job's units and its estimate, then hand the units to the pool."""
    from backend.services.ai import digest, estimate, settings as ai_settings

    run_followups = False
    with _session_factory() as db:
        user = db.get(User, job.user_id)
        book = db.get(Book, job.book_id)
        if user is None or book is None:
            with _lock:
                _finalize_locked(job, "error", "The book or the user no longer exists.")
            _mirror(job)
            return
        scope = digest.Scope(book=book) if job.current_book_only else digest.user_scope(db, user, book)
        units = digest.plan_units(db, scope, job.upto)
        model = ai_settings.effective_model(db, digest.FEATURE)
        figs = estimate.units_figures(units, model, estimate.history(db, model))
    with _lock:
        job.expanding = False
        job.total = figs["chapters"]
        job.est_chapters = figs["chapters"]
        job.est_cost_usd = figs["est_cost_usd"]
        job.est_seconds = figs["est_seconds"]
        job.version += 1
        if job.finished:
            pass
        elif job.cancel.is_set() or job.final is not None:
            _settle_locked(job)
        else:
            job.units = deque(units)
            run_followups = _settle_locked(job)   # nothing to read: straight to the follow-ups
            if not run_followups:
                _ensure_workers_locked()
    _mirror(job)
    if run_followups:
        _complete(job)


def _run_unit(job: Job, unit: Any, token: int) -> None:
    """One unit: one model call (or none, when its chapters got stored
    meanwhile), then the job's counters, spend and possibly its end."""
    from backend.services.ai import digest

    errors: list[tuple[int, str]] = []
    split: list[Any] = []
    unit_failed = False
    stop: Optional[tuple[str, str]] = None
    spend: Optional[tuple[int, float]] = None
    try:
        with _session_factory() as db:
            user = db.get(User, job.user_id)
            vol = db.get(Book, unit.book_id)
            first_title = db.query(BookChapter.title).filter(
                BookChapter.book_id == unit.book_id, BookChapter.idx == unit.idxs[0]).scalar()
            with _lock:
                if token in job.in_flight:
                    job.in_flight[token].update(title=vol.title if vol else None,
                                                chapter_title=first_title)
            try:
                if user is None or vol is None:
                    raise digest.DigestUnavailable("The book or the user no longer exists.")
                if len(unit.idxs) == 1:
                    rows = [digest.digest_chapter(db, user, vol, unit.idxs[0], job_id=job.id,
                                                  replace_partial=getattr(unit, "replace", False))]
                else:
                    rows = digest.digest_chapters(db, user, vol, list(unit.idxs), job_id=job.id)
                stored = {r.chapter_idx for r in rows}
                left_out = [i for i in unit.idxs if i not in stored]
                errors = [(i, LEFT_OUT) for i in left_out]
                unit_failed = len(left_out) == len(unit.idxs)
            except digest.UnitSplit as exc:
                # Nothing stored: its chapters are read one by one next.
                db.rollback()
                split = list(exc.units)
            except (AINotConfigured, AIDisabled, AIBudgetExceeded) as exc:
                db.rollback()
                stop = ("stopped", exc.message)
            except AIError as exc:
                db.rollback()
                errors = [(i, exc.message) for i in unit.idxs]
                unit_failed = True
            except Exception as exc:  # noqa: BLE001 - one bad unit never aborts the run
                db.rollback()
                log.warning("digest job %s: chapters %s of book %s failed (%s)",
                            job.id, list(unit.idxs), unit.book_id, type(exc).__name__)
                errors = [(i, str(exc) or type(exc).__name__) for i in unit.idxs]
                unit_failed = True
            try:
                spend = _job_spend(db, job.id)
            except Exception:  # noqa: BLE001
                db.rollback()
    finally:
        with _lock:
            _busy_books.discard(unit.book_id)
            job.in_flight.pop(token, None)
            for idx, message in errors:
                _record_error_locked(job, unit.book_id, idx, message)
            if spend is not None:
                job.calls, job.cost_usd = spend
            if stop is not None:
                _halt_locked(job, *stop)
            elif split:
                # One call, not a failure: the chapters go back to the front
                # of the job's units, one each (still counted in ``total``).
                job.done += len(unit.idxs) - len(split)
                job.consecutive = 0
                if not job.cancel.is_set() and job.final is None:
                    job.units.extendleft(reversed(split))
            else:
                job.done += len(unit.idxs)
                job.consecutive = job.consecutive + 1 if unit_failed else 0
                if job.consecutive >= MAX_CONSECUTIVE_FAILURES and job.final is None:
                    _halt_locked(job, "stopped", REPEATED_FAILURES)
            job.version += 1
            run_followups = _settle_locked(job)
            if not run_followups:
                _ensure_workers_locked()
    _mirror(job)
    if run_followups:
        _complete(job)


BOOK_INCOMPLETE = "Not every chapter of this book could be read, so no recap was written."


def _followup_gate(db: Session, name: str, book: Book) -> Optional[str]:
    """Why a follow-up must not run (recorded as skipped, nothing billed),
    else None. "Previously on" needs every chapter of the book itself
    digested; it uses whatever digests of earlier volumes exist."""
    if name != FOLLOWUP_PREVIOUSLY:
        return None
    from backend.services.ai import digest
    from backend.services import book_text
    n = len(book_text.book_chapters(book, db))
    cov = digest.coverage(db, digest.Scope(book=book), n)
    return BOOK_INCOMPLETE if digest.usable(cov) < cov["needed"] else None


def _complete(job: Job) -> None:
    """The follow-ups, in order, then ``done`` (``cancelled`` when the user
    cancelled meanwhile: the rest are skipped)."""
    from backend.services import ai

    with _session_factory() as db:
        user = db.get(User, job.user_id)
        book = db.get(Book, job.book_id)
        with _lock:
            followups = list(job.followups)
        for name in followups:
            if job.cancel.is_set() or user is None or book is None:
                break
            fn = _followups.get(name)
            gate = _followup_gate(db, name, book)
            if gate is not None:
                result = {"name": name, "status": "skipped", "message": gate}
            elif fn is None:
                result = {"name": name, "status": "skipped", "message": "Not available yet."}
            else:
                try:
                    with ai.job_context(job.id):
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
                job.version += 1
        try:
            spend = _job_spend(db, job.id)
        except Exception:  # noqa: BLE001
            spend = None
    with _lock:
        if spend is not None:
            job.calls, job.cost_usd = spend
        if not job.finished:
            _finalize_locked(job, "cancelled" if job.cancel.is_set() else "done")
    _mirror(job)
