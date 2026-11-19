"""Backoff for password guessing on the login, OPDS Basic and KOSync paths.

Keyed by (lowercased username, client IP), never by IP alone: behind a
reverse proxy every user shares one address. After THRESHOLD failures in a
row a key must wait before its next attempt, 1 s doubling per further
failure up to CAP seconds. A refused attempt does not check the password at
all and does not count as a failure. A success clears the key; a key idle
for IDLE_SECONDS is forgotten. In memory, one process (Tome runs one
uvicorn worker).
"""
import math
import threading
import time
from typing import Callable, Optional

from fastapi import HTTPException, Request, status

THRESHOLD = 5
BASE_SECONDS = 1.0
CAP_SECONDS = 60.0
IDLE_SECONDS = 15 * 60
_PRUNE_ABOVE = 1000

Key = tuple[str, str]


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        # key -> (consecutive failures, time of the last failure)
        self._entries: dict[Key, tuple[int, float]] = {}

    @staticmethod
    def key(username: Optional[str], ip: Optional[str]) -> Key:
        return ((username or "").strip().lower(), ip or "")

    def _wait_for(self, failures: int) -> float:
        if failures < THRESHOLD:
            return 0.0
        return min(CAP_SECONDS, BASE_SECONDS * (2 ** (failures - THRESHOLD)))

    def retry_after(self, key: Key) -> float:
        """Seconds this key must still wait, 0 when it may try now."""
        now = self.clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0.0
            failures, last = entry
            if now - last > IDLE_SECONDS:
                del self._entries[key]
                return 0.0
            return max(0.0, last + self._wait_for(failures) - now)

    def failure(self, key: Key) -> None:
        now = self.clock()
        with self._lock:
            failures, last = self._entries.get(key, (0, now))
            if now - last > IDLE_SECONDS:
                failures = 0
            self._entries[key] = (failures + 1, now)
            if len(self._entries) > _PRUNE_ABOVE:
                for k in [k for k, (_f, t) in self._entries.items() if now - t > IDLE_SECONDS]:
                    del self._entries[k]

    def success(self, key: Key) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


throttle = LoginThrottle()


def client_ip(request: Optional[Request]) -> str:
    """The address uvicorn reports (proxy headers only when uvicorn trusts the
    proxy, see FORWARDED_ALLOW_IPS). Never a raw X-Forwarded-For: a client
    could rotate it to dodge the backoff."""
    if request is not None and request.client:
        return request.client.host
    return ""


def check(key: Key) -> None:
    """Raise 429 with Retry-After when this key is backing off."""
    wait = throttle.retry_after(key)
    if wait > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again shortly.",
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )
