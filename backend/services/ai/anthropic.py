"""Anthropic implementation of :class:`AIProvider`.

The only module in Tome that imports the Anthropic SDK.

- Opus 5.5 always thinks; depth is set with ``output_config.effort``, which
  defaults to medium on that model, so it is always passed explicitly.
- JSON comes back through structured outputs (``output_config.format``), not
  forced tool use (a 400 on the current models).
- A safety-classifier decline is ``stop_reason == "refusal"``. Models that
  support it opt into the server-side fallback (``fallbacks: "default"``), so
  a declined request is retried on Anthropic's recommended model inside the
  same call; only a refusal of the whole chain reaches the caller.
- Calls stream and collect the final message. A non-streaming response sends
  nothing until the whole answer (thinking included) is ready, so a flat read
  timeout would cap the whole generation; streamed, ``TIMEOUT_SECONDS`` is the
  longest silence allowed between events, and a large series cleanup can take
  minutes without timing out. The SDK retries only the opening request, so a
  stalled stream is not re-sent (and re-billed).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
import httpx2

from backend.services.ai.provider import (
    AIKeyInvalid,
    AIProviderError,
    AIRefused,
    AIResult,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60.0  # per read: the longest gap between stream events
MAX_RETRIES = 2
VALIDATION_MODEL = "claude-opus-5-5"  # the default for every feature this release

# Models that accept the server-side fallback in its "default" form on the
# Claude API. Haiku has no server-side fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _provider_message(exc: anthropic.APIStatusError) -> str:
    """The provider's own error sentence, without the status-code preamble."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return str(getattr(exc, "message", "") or "unknown error")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float = TIMEOUT_SECONDS,
                 max_retries: int = MAX_RETRIES) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    # ── key check ────────────────────────────────────────────────────────────
    def validate_key(self) -> None:
        """count_tokens is free and fails on a bad key the same way a real
        call would."""
        try:
            self._client.messages.count_tokens(
                model=VALIDATION_MODEL,
                messages=[{"role": "user", "content": "ping"}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise AIKeyInvalid(f"Anthropic rejected this key: {_provider_message(exc)}") from None
        except anthropic.RateLimitError:
            raise AIProviderError("Anthropic is rate limiting this key. Try again in a minute.") from None
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                raise AIKeyInvalid(f"Anthropic rejected this key: {_provider_message(exc)}") from None
            raise AIProviderError(f"Anthropic returned an error ({exc.status_code}).") from None
        except anthropic.APIConnectionError:
            raise AIProviderError("Could not reach Anthropic.") from None

    # ── completion ───────────────────────────────────────────────────────────
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
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "output_config": output_config,
        }
        try:
            if model in _FALLBACK_MODELS:
                stream = self._client.beta.messages.stream(
                    betas=[_FALLBACK_BETA], fallbacks="default", **params,
                )
            else:
                stream = self._client.messages.stream(**params)
            with stream as s:
                response = s.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise AIProviderError("Anthropic rejected the configured key.") from None
        except anthropic.RateLimitError:
            raise AIProviderError("Anthropic is rate limiting this key. Try again in a minute.") from None
        except anthropic.APIStatusError as exc:
            logger.warning("AI call failed: feature=%s model=%s status=%s request_id=%s",
                           feature, model, exc.status_code, getattr(exc, "request_id", None))
            raise AIProviderError(f"Anthropic returned an error: {_provider_message(exc)}") from None
        except (anthropic.APITimeoutError, httpx2.TimeoutException):
            # Opening the stream timed out (after the SDK's retries), or the
            # stream went silent mid-answer (raised by the transport as is).
            logger.warning("AI call timed out: feature=%s model=%s", feature, model)
            raise AIProviderError("Anthropic took too long to answer. Try again in a minute.") from None
        except (anthropic.APIConnectionError, httpx2.TransportError):
            raise AIProviderError("Could not reach Anthropic.") from None

        usage = response.usage
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text") or None
        result = AIResult(
            text=text,
            parsed=None,
            model=response.model or model,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AIRefused(category=getattr(details, "category", None), result=result)
        if schema is not None:
            # These answers were billed: carry the usage so the ledger records it.
            if response.stop_reason == "max_tokens":
                raise AIProviderError("The model ran out of room before finishing its answer.",
                                      result=result)
            try:
                parsed = json.loads(text or "")
            except ValueError:
                raise AIProviderError("The model returned an answer Tome could not read.",
                                      result=result) from None
            if not isinstance(parsed, dict):
                raise AIProviderError("The model returned an answer Tome could not read.",
                                      result=result)
            result.parsed = parsed
        return result
