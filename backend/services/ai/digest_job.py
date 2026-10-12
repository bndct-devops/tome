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
  chapter twice at once. The volumes of one series are read in reading
  order ("serial"): a unit of volume N may start only when no unit of an
  earlier volume of the same series (``digest.series_key``, lower
  ``series_index``) is pending in any runnable job or in flight, so each
  volume starts from the glossary the earlier ones stored (no duplicate
  entities from parallel starts). Different series run in parallel.
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
  message (str) saying why it did nothing; a dict it returns is added to its
  result. Their calls carry the job id.
- Steps: ``step`` is ``reading`` while units run (``step_done`` /
  ``step_total`` = chapters), then each step follow-up sets its own:
  ``reconcile`` is ``merging`` (``reconcile.run``; ``step_done`` /
  ``step_total`` = its calls), ``volume_recaps`` is
  ``writing`` (``step_done``/``step_total`` = recaps). The job stays
  ``running`` through them and ends ``done`` after the last. A follow-up
  reads ``current_job_id()``, reports with ``step_progress`` and checks
  ``cancel_requested()`` between its calls: a cancel stops after the
  current call. A step follow-up stopped by ``AIBudgetExceeded`` /
  ``AINotConfigured`` / ``AIDisabled`` records ``stop_reason``, skips the
  rest and ends the job ``stopped`` (what it wrote stays).

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
from backend.services.ai.provider import (
    AIBudgetExceeded, AIDisabled, AIError, AIKeyInvalid, AINotConfigured,
)

log = logging.getLogger(__name__)

WORKERS = 4
# Volumes of one series are read in reading order (see the module docstring).
# Tests of the pool's parallelism turn it off.
SERIAL_VOLUMES = True
FOLLOWUP_PREVIOUSLY = "previously_recap"
FOLLOWUP_RECONCILE = "reconcile"
FOLLOWUP_VOLUME_RECAPS = "volume_recaps"

STEP_READING, STEP_MERGING, STEP_WRITING = "reading", "merging", "writing"
# The step a follow-up runs as; follow-ups not listed keep the current step.
FOLLOWUP_STEPS = {FOLLOWUP_RECONCILE: STEP_MERGING, FOLLOWUP_VOLUME_RECAPS: STEP_WRITING}

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
# book_id -> (series key, (series_index, book_id)): filled when a job expands;
# the serial-volumes rule reads it (a standalone or an unnumbered volume never
# waits). The book id breaks a tie: two volumes with one index read in turn.
_vol_order: dict[int, tuple[str, Optional[tuple[float, int]]]] = {}
_local = threading.local()                    # the job whose follow-ups this thread runs
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
    kind: str = "digest"            # ``ai_jobs.kind``: "digest", or "reread" (Read again)
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
    step: Optional[str] = None                # reading | merging | writing
    step_done: int = 0
    step_total: int = 0

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
            "kind": self.kind,
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
            "step": self.step,
            "step_done": self.step_done,
            "step_total": self.step_total,
            "created_at": _iso(_utc(self.created_at)),
            "started_at": _iso(_utc(self.started_at)),
            "finished_at": _iso(_utc(self.finished_at)),
        }

    def row_fields(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id, "book_id": self.book_id, "kind": self.kind,
            "scope": self.scope, "status": self.status, "upto_chapter": self.upto,
            "total": self.total, "done": self.done, "failed": self.failed,
            "calls": self.calls, "cost_usd": round(self.cost_usd, 6),
            "est_seconds": self.est_seconds, "est_cost_usd": self.est_cost_usd,
            "est_chapters": self.est_chapters, "stop_reason": self.stop_reason,
            "followups_json": json.dumps(self.followups_payload()),
            "step": self.step, "step_done": self.step_done, "step_total": self.step_total,
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
        "step": row.step, "step_done": row.step_done or 0, "step_total": row.step_total or 0,
        "created_at": _iso(row.created_at), "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


# ── public API ───────────────────────────────────────────────────────────────

def register_followup(name: str, fn: FollowupFn) -> None:
    """Register what a follow-up name does. ``fn(db, user, book)`` runs in a
    worker thread with its own session after the job's digests."""
    _followups[name] = fn


def current_job_id() -> Optional[str]:
    """The id of the job whose follow-ups this thread is running, else None."""
    job = getattr(_local, "job", None)
    return job.id if job is not None else None


def cancel_requested(job_id: Optional[str] = None) -> bool:
    """True when the job (``job_id``, else the one whose follow-ups this
    thread runs) was cancelled: a follow-up stops after its current call."""
    with _lock:
        job = _jobs.get(job_id) if job_id else getattr(_local, "job", None)
        return bool(job is not None and job.cancel.is_set())


def step_progress(done: int, total: Optional[int] = None) -> None:
    """A follow-up reports its step's progress (no-op outside a follow-up)."""
    job = getattr(_local, "job", None)
    if job is None:
        return
    with _lock:
        job.step_done = int(done)
        if total is not None:
            job.step_total = int(total)
        job.version += 1
    _mirror(job)


def latest_with_followup(db: Session, user_id: int, book_id: int, name: str,
                         finished: bool = True) -> Optional[dict[str, Any]]:
    """Snapshot of the newest job for (user, book) that had follow-up
    ``name`` (finished ones only when ``finished``), else None."""
    q = db.query(AIJob.id).filter(AIJob.user_id == user_id, AIJob.book_id == book_id,
                                  AIJob.followups_json.like(f'%"{name}"%'))
    if finished:
        q = q.filter(AIJob.status.notin_(LIVE_STATUSES))
    for (job_id,) in q.order_by(AIJob.created_at.desc()).limit(5):
        snap = get(job_id, db)
        if snap is not None and (not finished or snap["status"] not in ACTIVE):
            return snap
    return None


def enqueue(*, user_id: int, book_id: int, upto: int,
            followups: tuple[str, ...] | list[str] = (),
            current_book_only: bool = False, kind: str = "digest") -> dict[str, Any]:
    """Queue (or coalesce into) a digest job; returns its snapshot. ``kind``
    other than "digest" (a Read again passes "reread") also marks a job it
    coalesces into."""
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
            if kind != "digest":
                job.kind = kind
            job.version += 1
            target = job
            break
        if target is None:
            target = Job(id=uuid.uuid4().hex[:12], user_id=user_id, book_id=book_id, upto=upto,
                         followups=list(dict.fromkeys(followups)),
                         current_book_only=current_book_only, kind=kind)
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
    failures) does not count, nor does one whose reading was not what got
    cancelled: one past its reading step (cancelled while merging or
    writing), or one with nothing to read (``total`` 0 and a step past
    reading, or the reads-nothing shape "Merge duplicates" queues: ``upto``
    0 on the book alone with a step follow-up)."""
    row = (
        db.query(AIJob.status, AIJob.stop_reason, AIJob.step, AIJob.total,
                 AIJob.upto_chapter, AIJob.scope, AIJob.followups_json)
        .filter(AIJob.user_id == user_id, AIJob.book_id == book_id)
        .order_by(AIJob.created_at.desc())
        .first()
    )
    if row is None:
        return False
    status, reason, step, total, upto, scope, followups_json = row
    if not (status == "cancelled" or (status == "stopping" and reason is None)):
        return False
    if step not in (None, STEP_READING):
        return False
    # Not expanded yet (step None, total 0): a reading job unless it is the
    # reads-nothing shape.
    if total == 0 and upto == 0 and scope == "book":
        try:
            names = {f.get("name") if isinstance(f, dict) else f
                     for f in json.loads(followups_json or "[]")}
        except (ValueError, TypeError, AttributeError):
            names = set()
        if names & set(FOLLOWUP_STEPS):
            return False
    return True


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
        _vol_order.clear()
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


def _series_front_locked(runnable: list[Job]) -> dict[str, tuple[float, int]]:
    """Per series key, the lowest ``(series_index, book_id)`` with a unit
    pending (in a runnable job) or in flight: only that volume of the series
    may run (two volumes sharing an index never run side by side)."""
    front: dict[str, tuple[float, int]] = {}

    def see(book_id: int) -> None:
        key, idx = _vol_order.get(book_id, (None, None))
        if key is None or idx is None:
            return
        if key not in front or idx < front[key]:
            front[key] = idx

    for job in runnable:
        for unit in job.units:
            see(unit.book_id)
    for book_id in _busy_books:
        see(book_id)
    return front


def _eligible_locked(book_id: int, front: dict[str, tuple[float, int]]) -> bool:
    """A unit of ``book_id`` may start: no worker is reading that volume and
    no earlier volume of its series is pending or in flight."""
    if book_id in _busy_books:
        return False
    if not SERIAL_VOLUMES:
        return True
    key, idx = _vol_order.get(book_id, (None, None))
    return key is None or idx is None or key not in front or idx <= front[key]


def _take_locked() -> Optional[tuple[str, Job, Any, Optional[int]]]:
    """The next item for a worker: a unit (oldest job first, its units in
    order, on a volume no other worker is reading and with no earlier
    volume of its series still to read), else a queued job to expand, else
    None."""
    runnable = _runnable_locked()
    front = _series_front_locked(runnable)
    for job in runnable:
        for i, unit in enumerate(job.units):
            if not _eligible_locked(unit.book_id, front):
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
    runnable = _runnable_locked()
    front = _series_front_locked(runnable)
    free_volumes = {u.book_id for j in runnable for u in j.units
                    if _eligible_locked(u.book_id, front)}
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
        vol_order = {v.id: (digest.series_key(v),
                            (float(v.series_index), v.id) if v.series_index is not None else None)
                     for v in scope.volumes()}
        model = ai_settings.effective_model(db, digest.FEATURE)
        figs = estimate.units_figures(units, model, estimate.history(db, model))
    with _lock:
        job.expanding = False
        job.total = figs["chapters"]
        job.est_chapters = figs["chapters"]
        job.est_cost_usd = figs["est_cost_usd"]
        job.est_seconds = figs["est_seconds"]
        _vol_order.update(vol_order)
        if units:
            job.step, job.step_done, job.step_total = STEP_READING, job.done, job.total
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
            if job.step == STEP_READING:
                job.step_done, job.step_total = job.done, job.total
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

    stopped: Optional[str] = None
    _local.job = job
    try:
        with _session_factory() as db:
            user = db.get(User, job.user_id)
            book = db.get(Book, job.book_id)
            with _lock:
                followups = list(job.followups)
            for name in followups:
                if job.cancel.is_set() or user is None or book is None:
                    break
                if stopped is not None:
                    with _lock:
                        job.followup_results.append(
                            {"name": name, "status": "skipped", "message": stopped})
                        job.version += 1
                    continue
                result, stop = _run_followup(db, job, name, user, book)
                if stop is not None:
                    stopped = stop
                with _lock:
                    job.followup_results.append(result)
                    job.version += 1
                _mirror(job)
            try:
                spend = _job_spend(db, job.id)
            except Exception:  # noqa: BLE001
                spend = None
    finally:
        _local.job = None
    with _lock:
        if spend is not None:
            job.calls, job.cost_usd = spend
        if not job.finished:
            if job.cancel.is_set():
                _finalize_locked(job, "cancelled")
            elif stopped is not None:
                _finalize_locked(job, "stopped", stopped)
            else:
                _finalize_locked(job, "done")
    _mirror(job)


def _run_followup(db: Session, job: Job, name: str, user: User,
                  book: Book) -> tuple[dict[str, Any], Optional[str]]:
    """(result, stop reason) of one follow-up. A step follow-up sets the
    job's step first; one stopped by the budget or a missing key returns the
    stop reason (the job ends ``stopped``)."""
    from backend.services import ai

    step = FOLLOWUP_STEPS.get(name)
    if step is not None:
        with _lock:
            job.step, job.step_done, job.step_total = step, 0, 0
            job.version += 1
        _mirror(job)
    fn = _followups.get(name)
    gate = _followup_gate(db, name, book)
    if gate is not None:
        return {"name": name, "status": "skipped", "message": gate}, None
    if fn is None:
        return {"name": name, "status": "skipped", "message": "Not available yet."}, None
    try:
        with ai.job_context(job.id):
            out = fn(db, user, book)
    except (AINotConfigured, AIKeyInvalid, AIDisabled, AIBudgetExceeded) as exc:
        db.rollback()
        partial = getattr(exc, "followup_result", None) or {}
        return ({**partial, "name": name, "status": "failed", "message": exc.message},
                exc.message if step is not None else None)
    except AIError as exc:
        db.rollback()
        return {"name": name, "status": "failed", "message": exc.message}, None
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("digest job %s: follow-up %s failed", job.id, name)
        return {"name": name, "status": "failed", "message": type(exc).__name__}, None
    if isinstance(out, str):
        return {"name": name, "status": "skipped", "message": out}, None
    if isinstance(out, dict):
        return {**out, "name": name, "status": "done"}, None
    return {"name": name, "status": "done"}, None


def _reconcile_followup(db: Session, user: User, book: Book) -> dict[str, Any]:
    """Step "merging": the reconcile pass over the book's series (code-found
    candidates, model-confirmed merges). Imported here so a missing module
    fails only when the step runs."""
    from backend.services.ai import reconcile

    res = reconcile.run(db, user, book, job_id=current_job_id(),
                        on_progress=step_progress, should_stop=cancel_requested)
    return {"candidates": int(getattr(res, "candidates", 0) or 0),
            "merged": int(getattr(res, "merged", 0) or 0),
            "calls": int(getattr(res, "calls", 0) or 0),
            "merges": [{"from": a, "into": b} for a, b in getattr(res, "merges", None) or []]}


register_followup(FOLLOWUP_RECONCILE, _reconcile_followup)
