"""AI provider plumbing. Every model call in Tome goes through ``run_feature``.

``run_feature`` checks the switches, resolves the key, picks the model
(per-feature override or registry default), checks the monthly spend limits
(``budget.py``) against a conservative guess of the call's cost, calls the
provider, records a usage row (with the job and book it was for, and how
long it took), and lets the typed errors in ``provider.py`` propagate. The app
registers one exception handler for :class:`AIError` (``backend/main.py``),
so feature endpoints just call this and return.

Tests inject a fake with ``set_provider_override``; nothing then reaches a
real provider. Key resolution still runs, so "not configured" behaves the
same with a fake in place.
"""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from backend.models.user import User
from backend.services.ai import budget, registry, settings as ai_settings, usage
from backend.services.ai.provider import (
    AIBudgetExceeded,
    AIDisabled,
    AIError,
    AIKeyInvalid,
    AINotConfigured,
    AIProvider,
    AIProviderError,
    AIRefused,
    AIResult,
)

__all__ = [
    "AIBudgetExceeded", "AIDisabled", "AIError", "AIKeyInvalid", "AINotConfigured", "AIProvider",
    "AIProviderError", "AIRefused", "AIResult",
    "get_provider", "job_context", "make_provider", "run_feature", "set_provider_override",
]

# The background job whose calls are running in this thread (a digest job's
# follow-ups); ``run_feature`` stamps it on usage rows that name no job.
_current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar("ai_job_id", default=None)


@contextmanager
def job_context(job_id: str | None) -> Iterator[None]:
    token = _current_job.set(job_id)
    try:
        yield
    finally:
        _current_job.reset(token)


def _content_words(user_content: list[dict[str, Any]] | str) -> int:
    if isinstance(user_content, str):
        return len(user_content.split())
    return sum(len(str(b.get("text", "")).split()) for b in user_content if isinstance(b, dict))

_provider_override: AIProvider | None = None


def set_provider_override(provider: AIProvider | None) -> None:
    """Tests only: route every provider construction to ``provider``."""
    global _provider_override
    _provider_override = provider


def make_provider(api_key: str) -> AIProvider:
    """A provider for an explicit key (used to validate a key before storing it)."""
    if _provider_override is not None:
        return _provider_override
    from backend.services.ai.anthropic import AnthropicProvider
    return AnthropicProvider(api_key=api_key)


def _resolve(db: Session, user: User) -> tuple[AIProvider, str]:
    resolved = ai_settings.resolve_key(db, user)
    if resolved is None:
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    return make_provider(resolved.api_key), resolved.source


def get_provider(db: Session, user: User) -> AIProvider:
    """The provider for ``user``'s resolved key. Raises AINotConfigured."""
    return _resolve(db, user)[0]


def check_feature_available(db: Session, feature: str) -> registry.Feature:
    if not ai_settings.ai_enabled(db):
        raise AIDisabled("AI features are turned off on this server.")
    f = registry.get_feature(feature)
    if f is None:
        raise AIDisabled(f"Unknown AI feature: {feature}")
    if not ai_settings.feature_enabled(db, feature):
        raise AIDisabled(f"The AI feature '{f.label}' is turned off on this server.")
    return f


def run_feature(
    db: Session,
    user: User,
    feature: str,
    *,
    system: str,
    user_content: list[dict[str, Any]] | str,
    schema: dict | None = None,
    max_tokens: int = 4096,
    job_id: str | None = None,
    book_id: int | None = None,
    cost_guess: float | None = None,
    chapters: int = 1,
    words: int | None = None,
) -> AIResult:
    """One model call. ``job_id``/``book_id`` are stored on the usage row
    (``job_id`` defaults to the ``job_context``), and for a digest call
    ``words`` (chapter text words sent) and ``chapters`` (chapters covered),
    which the estimate's output history reads. ``cost_guess`` is the USD the
    spend-limit check assumes for this call; by default a conservative guess
    from the feature, the model, the words sent, ``chapters`` and
    ``max_tokens`` (a digest call's output term is its ``max_tokens``, a
    real upper bound). The guess is reserved until the usage row is written,
    so calls in flight count against the limits. Raises ``AIBudgetExceeded``
    before calling when a limit would be passed."""
    f = check_feature_available(db, feature)
    provider, key_source = _resolve(db, user)
    model = ai_settings.effective_model(db, feature)
    if cost_guess is None:
        from backend.services.ai import estimate
        cost_guess = estimate.call_cost_guess(feature, model, _content_words(user_content), chapters,
                                              max_tokens=max_tokens)
    token = budget.check_and_reserve(db, user, key_source, cost_guess)
    try:
        job_id = job_id or _current_job.get()
        extra: dict[str, Any] = {}
        if words is not None:
            extra = {"words_sent": int(words), "chapters": int(chapters)}
        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            result = provider.complete(
                feature=feature, model=model, effort=f.effort, system=system,
                user_content=user_content, schema=schema, max_tokens=max_tokens,
            )
        except AIError as exc:
            # A refusal or an answer Tome could not use (cut off, unreadable) can
            # still be billed; keep the ledger honest whenever usage came back.
            res = getattr(exc, "result", None)
            if res is not None:
                usage.record_usage(db, user_id=user.id, feature=feature, key_source=key_source,
                                   result=res, job_id=job_id, book_id=book_id,
                                   duration_ms=elapsed_ms(), **extra)
            raise
        except Exception as exc:  # an adapter bug must not surface as a bare 500 with internals
            raise AIProviderError(f"The AI provider failed: {type(exc).__name__}") from exc
        usage.record_usage(db, user_id=user.id, feature=feature, key_source=key_source, result=result,
                           job_id=job_id, book_id=book_id, duration_ms=elapsed_ms(), **extra)
        return result
    finally:
        # After the usage row is committed (or the call failed unbilled): the
        # recorded spend now stands in for the reservation.
        budget.release(token)
