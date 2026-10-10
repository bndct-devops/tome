"""AI provider plumbing. Every model call in Tome goes through ``run_feature``.

``run_feature`` checks the switches, resolves the key, picks the model
(per-feature override or registry default), calls the provider, records a
usage row, and lets the typed errors in ``provider.py`` propagate. The app
registers one exception handler for :class:`AIError` (``backend/main.py``),
so feature endpoints just call this and return.

Tests inject a fake with ``set_provider_override``; nothing then reaches a
real provider. Key resolution still runs, so "not configured" behaves the
same with a fake in place.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models.user import User
from backend.services.ai import registry, settings as ai_settings, usage
from backend.services.ai.provider import (
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
    "AIDisabled", "AIError", "AIKeyInvalid", "AINotConfigured", "AIProvider",
    "AIProviderError", "AIRefused", "AIResult",
    "get_provider", "make_provider", "run_feature", "set_provider_override",
]

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
) -> AIResult:
    f = check_feature_available(db, feature)
    provider, key_source = _resolve(db, user)
    model = ai_settings.effective_model(db, feature)
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
            usage.record_usage(db, user_id=user.id, feature=feature,
                               key_source=key_source, result=res)
        raise
    except Exception as exc:  # an adapter bug must not surface as a bare 500 with internals
        raise AIProviderError(f"The AI provider failed: {type(exc).__name__}") from exc
    usage.record_usage(db, user_id=user.id, feature=feature, key_source=key_source, result=result)
    return result
