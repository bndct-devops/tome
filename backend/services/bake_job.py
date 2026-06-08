"""Whole-library 'bake metadata to file' background job.

A single, serial, cancellable job rewrites the source files on disk with Tome's
metadata (see ``metadata_embed.bake_to_file``). It runs in a daemon thread so it
survives the request that started it — closing the browser tab or navigating
away never cancels it. Progress lives in module-global state (guarded by a lock)
and is read back via the status endpoint, so the UI is just a poller and can
reconnect at any time.

Single-process model: state is in-memory, so a server restart drops the job and
its progress. That is safe and cheap to recover from — ``metadata_synced_at``
makes a re-run skip everything already baked, so the admin just starts it again
and it resumes where it left off. (Tome runs a single uvicorn worker; SQLite is
single-writer, so there is no multi-process state to reconcile.)

Progress is weighted by *bytes*, not file count: a 300 MB CBZ costs ~150× an
EPUB, so a file-count bar/ETA would lie. We sum file sizes up front and track
bytes processed; the ETA is ``elapsed * bytes_left / bytes_done``.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from backend.core.database import SessionLocal
from backend.models.book import Book, BookFile
from backend.services.metadata_embed import BAKEABLE_FORMATS, bake_to_file

log = logging.getLogger(__name__)

# Cap on how many per-file issues (skipped/failed) we retain for the end screen.
_MAX_ISSUES = 500

_lock = threading.Lock()
_cancel = threading.Event()
_thread: Optional[threading.Thread] = None

# The single job's state. ``status`` is one of:
#   idle | running | done | cancelled | error
_state: dict = {"status": "idle"}


class BakeAlreadyRunning(Exception):
    pass


def _needs_bake(synced, updated) -> bool:
    return not (synced is not None and updated is not None and synced == updated)


def preflight() -> dict:
    """Cheap, DB-only summary of what a run would do, using stored file_size
    (no disk stat). Safe to call on page load."""
    with SessionLocal() as db:
        rows = (
            db.query(
                BookFile.file_size,
                BookFile.format,
                BookFile.metadata_synced_at,
                Book.updated_at,
            )
            .join(Book, BookFile.book_id == Book.id)
            .all()
        )
    bakeable = already = pending = 0
    pending_bytes = 0
    for size, fmt, synced, updated in rows:
        if (fmt or "").lower() not in BAKEABLE_FORMATS:
            continue
        bakeable += 1
        if _needs_bake(synced, updated):
            pending += 1
            pending_bytes += size or 0
        else:
            already += 1
    return {
        "bakeable_total": bakeable,
        "already_current": already,
        "pending": pending,
        "pending_bytes": pending_bytes,
    }


def _snapshot() -> dict:
    """A JSON-able copy of state with derived elapsed/ETA filled in."""
    s = dict(_state)
    if s.get("status") == "running" and s.get("started_at"):
        elapsed = time.time() - s["started_at"]
    elif s.get("started_at") and s.get("finished_at"):
        elapsed = s["finished_at"] - s["started_at"]
    else:
        elapsed = 0.0
    s["elapsed_seconds"] = round(elapsed, 1)

    eta = None
    if s.get("status") == "running":
        done_bytes = s.get("done_bytes", 0)
        total_bytes = s.get("total_bytes", 0)
        if done_bytes > 0 and total_bytes > done_bytes:
            eta = elapsed * (total_bytes - done_bytes) / done_bytes
    s["eta_seconds"] = round(eta, 1) if eta is not None else None
    return s


def get_status() -> dict:
    with _lock:
        return _snapshot()


def dismiss() -> dict:
    """Clear a finished run's summary so the page resets to idle. No-op while
    a run is in progress."""
    global _state
    with _lock:
        if _state.get("status") != "running":
            _state = {"status": "idle"}
        return _snapshot()


def request_cancel() -> bool:
    with _lock:
        if _state.get("status") == "running":
            _cancel.set()
            return True
        return False


def start(*, username: Optional[str] = None) -> dict:
    """Begin a whole-library bake. Raises BakeAlreadyRunning if one is active."""
    global _state, _thread
    with _lock:
        if _state.get("status") == "running":
            raise BakeAlreadyRunning()

        # Build the work list now (file_id, book_id, size) for files that need it.
        with SessionLocal() as db:
            rows = (
                db.query(
                    BookFile.id,
                    BookFile.book_id,
                    BookFile.file_size,
                    BookFile.format,
                    BookFile.metadata_synced_at,
                    Book.updated_at,
                )
                .join(Book, BookFile.book_id == Book.id)
                .all()
            )
        work: list[tuple[int, int, int]] = []
        total_bytes = 0
        for file_id, book_id, size, fmt, synced, updated in rows:
            if (fmt or "").lower() not in BAKEABLE_FORMATS:
                continue
            if _needs_bake(synced, updated):
                size = size or 0
                work.append((file_id, book_id, size))
                total_bytes += size

        _cancel.clear()
        _state = {
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "triggered_by": username,
            "total_files": len(work),
            "total_bytes": total_bytes,
            "done_files": 0,
            "done_bytes": 0,
            "baked": 0,
            "skipped": 0,
            "failed": 0,
            "current_file": None,
            "issues": [],
            "error": None,
        }
        _thread = threading.Thread(
            target=_run, args=(work,), name="bake-to-file", daemon=True
        )
        _thread.start()
        return _snapshot()


def _set_current(path: str) -> None:
    with _lock:
        _state["current_file"] = path


def _record(result, size: int) -> None:
    with _lock:
        _state["done_files"] += 1
        _state["done_bytes"] += size
        st = result.status
        if st == "baked":
            _state["baked"] += 1
        elif st == "failed":
            _state["failed"] += 1
        else:  # skipped | readonly
            _state["skipped"] += 1
        if st != "baked" and len(_state["issues"]) < _MAX_ISSUES:
            _state["issues"].append(
                {"path": result.file_path, "status": st, "reason": result.reason}
            )


def _finish(status: str, error: Optional[str] = None) -> None:
    with _lock:
        _state["status"] = status
        _state["finished_at"] = time.time()
        _state["current_file"] = None
        if error:
            _state["error"] = error


def _run(work: list[tuple[int, int, int]]) -> None:
    try:
        for file_id, book_id, size in work:
            if _cancel.is_set():
                _finish("cancelled")
                return
            try:
                with SessionLocal() as db:
                    bf = db.get(BookFile, file_id)
                    if bf is None:
                        _record(_Missing(f"file #{file_id}"), size)
                        continue
                    _set_current(bf.file_path)
                    book = db.get(Book, book_id)
                    if book is None:
                        _record(_Missing(bf.file_path), size)
                        continue
                    result = bake_to_file(book, bf)
                    if result.ok:
                        db.commit()
                    else:
                        db.rollback()
                _record(result, size)
            except Exception as e:  # noqa: BLE001 — one bad file never aborts the run
                log.exception("bake: unexpected error on file #%s", file_id)
                _record(_Failed(str(file_id), str(e)), size)
        _finish("done")
    except Exception as e:  # noqa: BLE001
        log.exception("bake: job crashed")
        _finish("error", error=str(e))


# Lightweight stand-ins so _record can treat every outcome uniformly.
class _Missing:
    status = "skipped"
    reason = "file missing"

    def __init__(self, path: str):
        self.file_path = path


class _Failed:
    status = "failed"

    def __init__(self, path: str, reason: str):
        self.file_path = path
        self.reason = reason
