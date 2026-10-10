"""Provider-neutral AI interface: the result shape, the protocol, the errors.

Feature modules never talk to an SDK. They call ``run_feature`` (see
``backend/services/ai/__init__.py``), which resolves a provider through
``get_provider`` / ``make_provider``. Only ``anthropic.py`` imports the
Anthropic SDK; an OpenAI-compatible adapter would be a second implementation
of :class:`AIProvider` and nothing else would change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AIResult:
    text: str | None
    parsed: dict | None       # set when a schema was given
    model: str                # the model that actually answered (may differ on fallback)
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class AIProvider(Protocol):
    def complete(
        self,
        *,
        feature: str,
        model: str,
        effort: str,
        system: str,
        user_content: list[dict[str, Any]] | str,
        schema: dict | None = None,
        max_tokens: int = 4096,
    ) -> AIResult: ...

    def validate_key(self) -> None: ...  # raises AIKeyInvalid / AIProviderError


class AIError(Exception):
    """Base for every AI failure the API layer maps to an HTTP status.

    Messages are plain English sentences safe to show a user. They never
    contain a key.
    """

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AIDisabled(AIError):
    """AI is switched off (env, instance or the feature itself)."""

    status_code = 404


class AINotConfigured(AIError):
    """No key resolves for this user."""

    status_code = 409


class AIKeyInvalid(AIError):
    """The provider rejected a key during validation."""

    status_code = 400


class AIRefused(AIError):
    """The model declined the request (stop_reason == "refusal")."""

    status_code = 422

    def __init__(self, message: str = "The model declined this request.",
                 *, category: str | None = None, result: AIResult | None = None) -> None:
        super().__init__(message)
        self.category = category
        # Usage of the refused call, when the provider reported one, so the
        # ledger still records what was billed.
        self.result = result


class AIProviderError(AIError):
    """The provider could not be reached or returned an error."""

    status_code = 502

    def __init__(self, message: str, *, result: AIResult | None = None) -> None:
        super().__init__(message)
        # Usage of a call that was answered (and billed) but could not be used,
        # such as a max_tokens cut-off or unreadable JSON, so the ledger still
        # records it. None when no response came back.
        self.result = result
