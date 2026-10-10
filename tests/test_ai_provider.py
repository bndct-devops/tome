"""AnthropicProvider request shape and response handling, against a mocked
SDK client (no network)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from backend.services.ai.anthropic import AnthropicProvider
from backend.services.ai.provider import AIKeyInvalid, AIProviderError, AIRefused

SCHEMA = {"type": "object", "properties": {"greeting": {"type": "string"}},
          "required": ["greeting"], "additionalProperties": False}


def _answer_with(messages: MagicMock, response) -> None:
    """Make ``messages.stream(...)`` yield ``response`` as its final message."""
    messages.stream.return_value.__enter__.return_value.get_final_message.return_value = response


def _provider() -> tuple[AnthropicProvider, MagicMock]:
    p = object.__new__(AnthropicProvider)  # skip __init__: the real client is blocked in tests
    client = MagicMock()
    p._client = client
    return p, client


def _response(text: str | None, stop_reason: str = "end_turn", model: str = "claude-opus-5-5",
              stop_details=None):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    usage = SimpleNamespace(input_tokens=120, output_tokens=30,
                            cache_read_input_tokens=None, cache_creation_input_tokens=5)
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model,
                           usage=usage, stop_details=stop_details)


def test_opus_call_uses_effort_schema_and_default_fallback():
    p, client = _provider()
    _answer_with(client.beta.messages, _response('{"greeting": "hello"}'))
    r = p.complete(feature="fix_book", model="claude-opus-5-5", effort="high",
                   system="sys", user_content="hi", schema=SCHEMA)
    kwargs = client.beta.messages.stream.call_args.kwargs
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["output_config"] == {"effort": "high",
                                       "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "thinking" not in kwargs and "tool_choice" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert r.parsed == {"greeting": "hello"}
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens) == (120, 30, 0, 5)


def test_haiku_call_has_no_fallback():
    p, client = _provider()
    _answer_with(client.messages, _response("plain", model="claude-haiku-5-5"))
    r = p.complete(feature="x", model="claude-haiku-5-5", effort="low", system="s", user_content="u")
    assert "fallbacks" not in client.messages.stream.call_args.kwargs
    assert r.text == "plain" and r.parsed is None
    client.beta.messages.stream.assert_not_called()


def test_refusal_raises_with_usage():
    p, client = _provider()
    _answer_with(client.beta.messages, _response(
        None, stop_reason="refusal", stop_details=SimpleNamespace(category="bio")))
    with pytest.raises(AIRefused) as ei:
        p.complete(feature="x", model="claude-opus-5-5", effort="high", system="s",
                   user_content="u", schema=SCHEMA)
    assert ei.value.category == "bio"
    assert ei.value.result is not None and ei.value.result.input_tokens == 120
    assert ei.value.message == "The model declined this request."


def test_truncated_or_unparseable_json_is_a_provider_error_carrying_usage():
    p, client = _provider()
    _answer_with(client.beta.messages, _response('{"greet', stop_reason="max_tokens"))
    with pytest.raises(AIProviderError) as ei:
        p.complete(feature="x", model="claude-opus-5-5", effort="high", system="s",
                   user_content="u", schema=SCHEMA)
    assert ei.value.result is not None
    assert (ei.value.result.input_tokens, ei.value.result.output_tokens) == (120, 30)
    _answer_with(client.beta.messages, _response("not json"))
    with pytest.raises(AIProviderError) as ei:
        p.complete(feature="x", model="claude-opus-5-5", effort="high", system="s",
                   user_content="u", schema=SCHEMA)
    assert ei.value.result is not None and ei.value.result.output_tokens == 30


def test_silent_stream_is_a_timeout_error_not_a_connection_error():
    p, client = _provider()
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.side_effect = (
        httpx2.ReadTimeout("silent", request=req))
    with pytest.raises(AIProviderError) as ei:
        p.complete(feature="x", model="claude-opus-5-5", effort="high", system="s",
                   user_content="u", schema=SCHEMA)
    assert "took too long" in ei.value.message
    client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.side_effect = (
        anthropic.APITimeoutError(request=req))
    with pytest.raises(AIProviderError) as ei:
        p.complete(feature="x", model="claude-opus-5-5", effort="high", system="s",
                   user_content="u", schema=SCHEMA)
    assert "took too long" in ei.value.message


def test_billed_failure_is_recorded_in_the_ledger(db, admin_user):
    from backend.models.ai_usage import AIUsage
    from backend.services import ai
    from backend.services.ai import settings as ai_settings
    from backend.services.ai.provider import AIResult
    from tests.ai_fake import FakeProvider

    user, _ = admin_user
    ai_settings.set_user_key(db, user, "sk-ant-test-ledger-000000000000Zz99")
    billed = AIResult(text='{"greet', parsed=None, model="claude-opus-5-5", input_tokens=900,
                      output_tokens=4096, cache_read_tokens=0, cache_write_tokens=0)
    ai.set_provider_override(FakeProvider(error=AIProviderError("cut off", result=billed)))
    with pytest.raises(AIProviderError):
        ai.run_feature(db, user, "fix_book", system="s", user_content="u", schema=SCHEMA)
    rows = db.query(AIUsage).filter(AIUsage.user_id == user.id).all()
    assert len(rows) == 1 and rows[0].output_tokens == 4096

    # An error with no response behind it records nothing.
    ai.set_provider_override(FakeProvider(error=AIProviderError("Could not reach Anthropic.")))
    with pytest.raises(AIProviderError):
        ai.run_feature(db, user, "fix_book", system="s", user_content="u", schema=SCHEMA)
    assert db.query(AIUsage).filter(AIUsage.user_id == user.id).count() == 1


def _status_error(cls, status: int, message: str):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/count_tokens")
    resp = httpx2.Response(status, request=req)
    body = {"type": "error", "error": {"type": "authentication_error", "message": message}}
    return cls(message=f"Error code: {status}", response=resp, body=body)


def test_validate_key_maps_auth_error_to_key_invalid_with_provider_message():
    p, client = _provider()
    client.messages.count_tokens.side_effect = _status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    with pytest.raises(AIKeyInvalid) as ei:
        p.validate_key()
    assert "invalid x-api-key" in ei.value.message


def test_validate_key_server_error_is_provider_error():
    p, client = _provider()
    client.messages.count_tokens.side_effect = _status_error(anthropic.InternalServerError, 500, "overloaded")
    with pytest.raises(AIProviderError):
        p.validate_key()
    client.messages.count_tokens.side_effect = None
    p.validate_key()  # success path: no exception
