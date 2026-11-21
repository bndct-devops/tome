"""Linking a CrossPoint reader to a Tome account with a code (experimental).

The reader cannot type a password, so the plugin signs in the way a TV app
does (RFC 8628, which is the flow the firmware implements):

1. the reader asks for a code (``start``) and shows it, with a QR code of the
   page to open;
2. the user opens that page on a phone, signed in to Tome, checks the code
   and confirms (``approve``);
3. the reader, which has been asking every few seconds (``poll``), receives a
   device key and stores it on its SD card.

Two values are involved and they do different jobs. The ``user_code`` is short
and shown on the reader; it only ever travels from a signed-in user to Tome,
so guessing it gets an attacker nothing but a link of their OWN account. The
``device_code`` is long and random and never shown; only the reader that asked
for it can collect the key.

Pending links live in memory: they last ten minutes, a restart only means the
user starts again, and nothing has to be migrated. Like the bake job this
assumes one server process.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from backend.models.quick_connect import generate_code

PENDING_TTL = 600          # seconds a code can be confirmed
POLL_INTERVAL = 5          # seconds the reader waits between polls
MAX_PENDING = 200          # starting needs no sign-in: bound what can pile up (see start)
MAX_WRONG_CODES = 10       # per user, per PENDING_TTL
READER_KEY_LABEL = "CrossPoint reader"


class Busy(Exception):
    """Every waiting link is confirmed and about to be collected (in practice never)."""


@dataclass
class _Pending:
    user_code: str
    expires_at: float
    user_id: Optional[int] = None


_lock = threading.Lock()
_pending: dict[str, _Pending] = {}          # sha256(device_code) -> pending link
_wrong: dict[int, list[float]] = {}         # user id -> times of wrong codes


def _hash(device_code: str) -> str:
    return hashlib.sha256(device_code.encode()).hexdigest()


def _prune(now: float) -> None:
    for key in [k for k, p in _pending.items() if p.expires_at <= now]:
        del _pending[key]


def normalise(code: object) -> str:
    """A user code as typed: case and the spaces or dashes people add do not matter."""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def start(now: Optional[float] = None) -> tuple[str, str]:
    """A reader asks to be linked. Returns (device_code, user_code)."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        if len(_pending) >= MAX_PENDING:
            # Starting needs no sign-in, so anyone can fill the list. Make room
            # by dropping the oldest link nobody has confirmed, rather than
            # refusing readers: a flood then only shortens how long a code
            # lasts, it cannot switch linking off.
            unconfirmed = [k for k, p in _pending.items() if p.user_id is None]
            if not unconfirmed:
                raise Busy()
            del _pending[min(unconfirmed, key=lambda k: _pending[k].expires_at)]
        taken = {p.user_code for p in _pending.values()}
        user_code = generate_code()
        while user_code in taken:
            user_code = generate_code()
        device_code = secrets.token_urlsafe(32)
        _pending[_hash(device_code)] = _Pending(user_code=user_code, expires_at=now + PENDING_TTL)
        return device_code, user_code


def _find(user_code: str, now: float) -> Optional[_Pending]:
    for pending in _pending.values():
        if pending.expires_at > now and secrets.compare_digest(pending.user_code, user_code):
            return pending
    return None


def too_many_wrong(user_id: int, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        recent = [t for t in _wrong.get(user_id, []) if now - t < PENDING_TTL]
        _wrong[user_id] = recent
        return len(recent) >= MAX_WRONG_CODES


def _note_wrong(user_id: int, now: float) -> None:
    _wrong.setdefault(user_id, []).append(now)


def waiting(user_code: object, user_id: int, now: Optional[float] = None) -> bool:
    """Is there a link waiting under this code that nobody has confirmed yet?
    A miss counts as a wrong code for ``user_id``."""
    now = time.time() if now is None else now
    code = normalise(user_code)
    with _lock:
        _prune(now)
        pending = _find(code, now) if code else None
        if pending is None or pending.user_id is not None:
            _note_wrong(user_id, now)
            return False
        return True


def approve(user_code: object, user_id: int, now: Optional[float] = None) -> bool:
    """The signed-in user confirms the code their reader shows. False when no
    such link is waiting (never offered, expired, or already confirmed)."""
    now = time.time() if now is None else now
    code = normalise(user_code)
    with _lock:
        _prune(now)
        pending = _find(code, now) if code else None
        if pending is None or pending.user_id is not None:
            _note_wrong(user_id, now)
            return False
        pending.user_id = user_id
        return True


def poll(device_code: object, now: Optional[float] = None) -> tuple[str, Optional[int]]:
    """The reader asks whether it has been confirmed. Returns ("pending",
    None), ("expired", None) or, exactly once, ("approved", user_id)."""
    now = time.time() if now is None else now
    if not isinstance(device_code, str) or not device_code:
        return "expired", None
    key = _hash(device_code)
    with _lock:
        _prune(now)
        pending = _pending.get(key)
        if pending is None:
            return "expired", None
        if pending.user_id is None:
            return "pending", None
        del _pending[key]
        return "approved", pending.user_id


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _pending.clear()
        _wrong.clear()
