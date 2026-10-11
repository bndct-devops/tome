"""Monthly AI spend limits.

Two limits, both in USD for the current calendar month (UTC), both optional:

- the user's (``user_limit``): everything that user spends, whatever key
  pays for it. The lower of the user's own ``User.ai_monthly_limit_usd`` and
  the cap an admin set on them, ``User.ai_admin_limit_usd`` (the user can
  change the first, never the second);
- the instance key's (``ai.budget.instance_monthly_usd``): everything charged
  to the instance key, by every user.

``run_feature`` calls ``check_and_reserve`` before every model call with a
conservative guess of what the call costs (``estimate.call_cost_guess``); a
call that would pass a limit raises ``AIBudgetExceeded`` (409, ``reason:
"budget"``) and nothing is sent. Otherwise the guess is held in an in-memory
reservation ledger until the call's usage row is written (``release``), so
calls running side by side (the digest workers) count each other: the check
is spent + reserved + this call's guess. The digest job stops on the error
with its message; the automatic triggers ask ``allows`` first and do not
enqueue at all. The ledger is per process (one uvicorn worker, as the job
runner already assumes).
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.models.user import User
from backend.services.ai import settings as ai_settings, usage
from backend.services.ai.provider import AIBudgetExceeded

_EPS = 1e-9

# ── reservations: calls checked but not yet recorded ─────────────────────────

_lock = threading.RLock()
_reservations: dict[int, tuple[int, str, float]] = {}   # token -> (user_id, key_source, usd)
_tokens = itertools.count(1)


def reserve(user_id: int, key_source: str, guess: float) -> int:
    """Hold ``guess`` USD against ``user_id`` (and the instance key when
    ``key_source`` is "instance") until ``release``. Returns the token."""
    with _lock:
        token = next(_tokens)
        _reservations[token] = (user_id, key_source, max(0.0, float(guess)))
        return token


def release(token: int | None) -> None:
    if token is None:
        return
    with _lock:
        _reservations.pop(token, None)


def reserved(*, user_id: int | None = None, key_source: str | None = None) -> float:
    """USD held by calls in flight, by one user and/or on one key source."""
    with _lock:
        return sum(usd for uid, src, usd in _reservations.values()
                   if (user_id is None or uid == user_id)
                   and (key_source is None or src == key_source))


def reset_reservations() -> None:
    """Tests only."""
    with _lock:
        _reservations.clear()


def effective_user_limit(user: User) -> float | None:
    """The lower of the user's own limit and an admin's cap (None = unset)."""
    vals = [v for v in (user.ai_monthly_limit_usd, user.ai_admin_limit_usd) if v is not None]
    return min(vals) if vals else None


def _no_limits(db: Session, user: User) -> bool:
    return effective_user_limit(user) is None and ai_settings.instance_monthly_budget(db) is None


@dataclass
class Budget:
    user_limit: float | None
    user_spent: float
    instance_limit: float | None
    instance_spent: float
    key_source: str | None       # "user" | "instance" | None (no key resolves)

    @property
    def instance_applies(self) -> bool:
        return self.key_source == "instance" and self.instance_limit is not None

    @property
    def remaining(self) -> float | None:
        """USD left under the limits that apply to this user's calls; None
        when no limit applies."""
        left: list[float] = []
        if self.user_limit is not None:
            left.append(max(0.0, self.user_limit - self.user_spent))
        if self.instance_applies:
            left.append(max(0.0, self.instance_limit - self.instance_spent))  # type: ignore[operator]
        return min(left) if left else None

    def exceeded_by(self, cost: float) -> AIBudgetExceeded | None:
        """The error a call (or job) costing ``cost`` would hit, else None."""
        if self.user_limit is not None and self.user_spent + cost > self.user_limit + _EPS:
            return AIBudgetExceeded(
                f"Monthly AI budget reached ({self.user_spent:.2f} of {self.user_limit:.2f} USD). "
                "Raise it in Settings > AI.",
                which="user", spent=self.user_spent, limit=self.user_limit)
        if self.instance_applies and self.instance_spent + cost > self.instance_limit + _EPS:  # type: ignore[operator]
            return AIBudgetExceeded(
                f"The server's monthly AI budget is reached ({self.instance_spent:.2f} of "
                f"{self.instance_limit:.2f} USD). An admin can raise it in Settings > AI.",
                which="instance", spent=self.instance_spent, limit=self.instance_limit or 0.0)
        return None


def status(db: Session, user: User, key_source: str | None = None) -> Budget:
    """The limits and this month's spend for ``user``, the spend counting
    the reservations of calls in flight. ``key_source`` is the source their
    calls are charged to (resolved when not given)."""
    if key_source is None:
        resolved = ai_settings.resolve_key(db, user)
        key_source = resolved.source if resolved else None
    inst_limit = ai_settings.instance_monthly_budget(db)
    return Budget(
        user_limit=effective_user_limit(user),
        user_spent=usage.month_spend(db, user_id=user.id) + reserved(user_id=user.id),
        instance_limit=inst_limit,
        instance_spent=(usage.month_spend(db, key_source="instance") + reserved(key_source="instance")
                        if inst_limit is not None else 0.0),
        key_source=key_source,
    )


def check(db: Session, user: User, key_source: str, cost: float) -> None:
    """Raise ``AIBudgetExceeded`` when a call costing ``cost`` would pass a
    limit that applies to ``user`` calling with ``key_source`` (spent and
    reserved both count)."""
    if _no_limits(db, user):
        return
    err = status(db, user, key_source).exceeded_by(cost)
    if err is not None:
        raise err


def check_and_reserve(db: Session, user: User, key_source: str, cost: float) -> int:
    """``check`` then ``reserve`` as one step under the ledger lock, so two
    calls checked side by side cannot both pass on the same headroom.
    Returns the token for ``release``."""
    with _lock:
        check(db, user, key_source, cost)
        return reserve(user.id, key_source, cost)


def allows(db: Session, user: User, cost: float) -> bool:
    """False when work costing ``cost`` would pass a limit (the automatic
    triggers skip enqueuing then)."""
    if _no_limits(db, user):
        return True
    return status(db, user).exceeded_by(cost) is None


def remaining(db: Session, user: User) -> float | None:
    if _no_limits(db, user):
        return None
    return status(db, user).remaining


def limits_payload(db: Session, user: User) -> dict[str, Any]:
    """``limits`` of GET /api/ai/usage. The instance figures are shown to
    admins and to members whose calls can be charged to the instance key."""
    b = status(db, user)
    show_instance = ai_settings.can_use_instance_key(db, user)
    inst_limit = ai_settings.instance_monthly_budget(db) if show_instance else None
    return {
        # The user's own limit, the admin's cap on them, and the lower of
        # the two (the one that applies).
        "user_monthly_usd": user.ai_monthly_limit_usd,
        "admin_monthly_usd": user.ai_admin_limit_usd,
        "effective_monthly_usd": b.user_limit,
        "user_spent_usd": round(usage.month_spend(db, user_id=user.id), 4),
        "instance_monthly_usd": inst_limit,
        "instance_spent_usd": (round(usage.month_spend(db, key_source="instance"), 4)
                               if show_instance else None),
        "remaining_usd": None if b.remaining is None else round(b.remaining, 4),
    }
