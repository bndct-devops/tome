"""Files the Bindery review page is working on, so auto-import leaves them alone.

An in-memory ``{relative_path: last_touch}`` map (monotonic clock) under a
lock. The review endpoints (``POST /bindery/preview``, ``POST /ai/identify``,
``POST /bindery/accept``) touch the paths they handle; accept and reject
release a path once the file has left the inbox. A path counts as held for
``REVIEW_TTL_SECONDS`` after its last touch, so an abandoned review frees the
file for the next auto-import tick on its own.

Single-process state, like the bake and digest jobs: it assumes one uvicorn
worker, and a restart forgets every hold (the next tick may then import a
file someone was reviewing, which is the pre-lock behaviour).
"""
from __future__ import annotations

import threading
import time
from pathlib import PurePath
from typing import Iterable

REVIEW_TTL_SECONDS = 1800

_lock = threading.Lock()
_touched: dict[str, float] = {}

# Indirection so tests can move the clock.
_now = time.monotonic


def _key(path: str) -> str:
    """Relative paths as the Bindery API uses them (``str(Path.relative_to)``)."""
    return str(PurePath(path))


def touch(paths: Iterable[str]) -> None:
    now = _now()
    with _lock:
        for p in paths:
            if p:
                _touched[_key(p)] = now


def is_held(path: str) -> bool:
    key = _key(path)
    now = _now()
    with _lock:
        at = _touched.get(key)
        if at is None:
            return False
        if now - at < REVIEW_TTL_SECONDS:
            return True
        del _touched[key]
        return False


def release(paths: Iterable[str]) -> None:
    with _lock:
        for p in paths:
            if p:
                _touched.pop(_key(p), None)


def held_paths() -> list[str]:
    """Currently held paths (for logs and tests)."""
    now = _now()
    with _lock:
        return sorted(k for k, at in _touched.items() if now - at < REVIEW_TTL_SECONDS)


def reset() -> None:
    with _lock:
        _touched.clear()
