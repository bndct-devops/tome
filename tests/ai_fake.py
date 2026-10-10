"""A stand-in AI provider for tests. Never touches the network.

Usage:
    from tests.ai_fake import FakeProvider
    fake = FakeProvider(responses=[{"title": "The Quiet Harbour"}])
    ai.set_provider_override(fake)   # the conftest guard resets it after each test

``responses`` is a list consumed in order, or a callable
``(feature, user_content, schema) -> dict``. Set ``valid=False`` to make
``validate_key`` reject, ``refuse=True`` to return a refusal, or ``error`` to
raise any AIError from ``complete``.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from backend.services.ai.provider import AIError, AIKeyInvalid, AIRefused, AIResult


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responses: list[dict] | Callable[[str, Any, dict | None], dict] | None = None,
        *,
        valid: bool = True,
        refuse: bool = False,
        error: AIError | None = None,
        model: str | None = None,
        input_tokens: int = 1000,
        output_tokens: int = 200,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        self.responses = responses if responses is not None else []
        self.valid = valid
        self.refuse = refuse
        self.error = error
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.calls: list[dict[str, Any]] = []
        self.validate_calls = 0

    def validate_key(self) -> None:
        self.validate_calls += 1
        if not self.valid:
            raise AIKeyInvalid("Anthropic rejected this key: invalid x-api-key")

    def _result(self, model: str, parsed: dict | None, text: str | None) -> AIResult:
        return AIResult(
            text=text, parsed=parsed, model=self.model or model,
            input_tokens=self.input_tokens, output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens, cache_write_tokens=self.cache_write_tokens,
        )

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
    ) -> AIResult:
        self.calls.append({
            "feature": feature, "model": model, "effort": effort, "system": system,
            "user_content": user_content, "schema": schema, "max_tokens": max_tokens,
        })
        if self.error is not None:
            raise self.error
        if self.refuse:
            raise AIRefused(category="general_harms", result=self._result(model, None, None))
        if callable(self.responses):
            payload = self.responses(feature, user_content, schema)
        elif self.responses:
            payload = self.responses.pop(0)
        else:
            payload = {}
        text = json.dumps(payload)
        return self._result(model, payload if schema is not None else None, text)
