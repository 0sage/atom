"""Tests for the OpenAI Codex provider backend."""

import asyncio
import inspect
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from atom.providers.base import LLMProvider, ProviderCallContext
from atom.providers.openai_codex_provider import (
    CODEX_ORIGINATOR,
    DEFAULT_CODEX_URL,
    OpenAICodexProvider,
    _build_reasoning_options,
    _codex_error_response,
    _codex_log_summary,
    _CodexHTTPError,
    _prompt_cache_key,
    _retained_compaction_messages,
    _should_retry_status,
    _without_response_item_ids,
    strip_model_prefix,
)
from atom.providers.openai_responses import build_responses_state

_TOKEN = "sk-secret-access-token"
_ACCOUNT = "acct_123"


class _FakeToken:
    def __init__(self, access: str = _TOKEN, account_id: str | None = _ACCOUNT):
        self.access = access
        self.refresh = "refresh-secret"
        self.expires = 0
        self.account_id = account_id


def _sse_body(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


_COMPLETED = [
    {"type": "response.output_text.delta", "delta": "hi"},
    {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi"}],
        },
    },
    {"type": "response.completed", "response": {"status": "completed"}},
]


def _transport(
    captured: dict[str, Any],
    *,
    status: int = 200,
    events: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        captured.setdefault("bodies", []).append(captured["body"])
        if status != 200:
            return httpx.Response(status, content=body or b"{}", headers=headers or {})
        return httpx.Response(
            200,
            content=_sse_body(events if events is not None else _COMPLETED),
            headers={"content-type": "text/event-stream"},
        )

    return httpx.MockTransport(handler)


def _run(
    provider: OpenAICodexProvider,
    transport: httpx.MockTransport,
    *,
    token: _FakeToken | None = None,
    stream: bool = False,
    **kwargs: Any,
):
    """Drive one provider call against a mock transport."""
    real_client = httpx.AsyncClient

    def _client(*args: Any, **client_kwargs: Any) -> httpx.AsyncClient:
        client_kwargs.pop("verify", None)
        return real_client(*args, **client_kwargs, transport=transport)

    with (
        patch(
            "atom.providers.openai_codex_provider.get_codex_token",
            return_value=token or _FakeToken(),
        ),
        patch("httpx.AsyncClient", _client),
    ):
        call = provider.chat_stream if stream else provider.chat
        return asyncio.run(call(**kwargs))


# ======================================================================
# Request shape
# ======================================================================


class TestRequestShape:
    def test_posts_to_codex_endpoint_with_oauth_headers(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            messages=[{"role": "user", "content": "hello"}],
        )

        assert captured["url"] == DEFAULT_CODEX_URL
        headers = captured["headers"]
        assert headers["authorization"] == f"Bearer {_TOKEN}"
        assert headers["chatgpt-account-id"] == _ACCOUNT
        assert headers["openai-beta"] == "responses=experimental"
        assert headers["originator"] == CODEX_ORIGINATOR
        assert headers["accept"] == "text/event-stream"

    def test_originator_is_atom_not_the_upstream_default(self):
        assert CODEX_ORIGINATOR == "atom"

    def test_account_header_omitted_when_token_lacks_account_id(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            token=_FakeToken(account_id=None),
            messages=[{"role": "user", "content": "hello"}],
        )
        assert "chatgpt-account-id" not in captured["headers"]

    def test_body_uses_stateless_streaming_contract(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            messages=[{"role": "user", "content": "hello"}],
        )

        body = captured["body"]
        assert body["store"] is False
        assert body["stream"] is True
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["parallel_tool_calls"] is True
        assert body["tool_choice"] == "auto"
        assert body["prompt_cache_key"]

    def test_model_prefix_is_stripped_on_the_wire(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}],
            model="openai-codex/gpt-5.6-sol",
        )
        assert captured["body"]["model"] == "gpt-5.6-sol"

    def test_extra_body_overrides_win(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(extra_body={"text": {"verbosity": "low"}}),
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert captured["body"]["text"] == {"verbosity": "low"}

    def test_extra_headers_are_merged(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(extra_headers={"x-trace": "abc"}),
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert captured["headers"]["x-trace"] == "abc"

    def test_tools_are_converted_to_flat_responses_format(self):
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}],
            tools=[{
                "type": "function",
                "function": {"name": "read_file", "description": "d", "parameters": {}},
            }],
        )
        assert captured["body"]["tools"] == [
            {"type": "function", "name": "read_file", "description": "d", "parameters": {}}
        ]

    def test_temperature_is_not_sent(self):
        """The Codex backend drives sampling itself; temperature is not on the wire."""
        captured: dict[str, Any] = {}
        _run(
            OpenAICodexProvider(),
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.1,
        )
        assert "temperature" not in captured["body"]


# ======================================================================
# TLS posture — the one behavior deliberately not ported from upstream
# ======================================================================


class TestTlsPosture:
    def test_no_verification_bypass_exists_in_the_module(self):
        """A bearer token is never resent over an unverified channel."""
        from atom.providers import openai_codex_provider as module

        source = inspect.getsource(module)
        assert "verify=False" not in source
        assert "CERTIFICATE_VERIFY_FAILED" not in source

    def test_certificate_failure_is_a_hard_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

        captured: dict[str, Any] = {}
        response = _run(
            OpenAICodexProvider(),
            httpx.MockTransport(handler),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert captured == {}
        assert response.finish_reason == "error"
        assert response.error_kind == "connection"


# ======================================================================
# Response handling
# ======================================================================


class TestResponseHandling:
    def test_parses_content_and_builds_replayable_state(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}),
            messages=[{"role": "user", "content": "hi"}],
            model="openai-codex/gpt-5.6-sol",
        )

        assert response.content == "hi"
        assert response.finish_reason == "stop"
        assert response.provider_state is not None
        assert response.provider_state.model == "gpt-5.6-sol"
        assert response.provider_state.provider.startswith("openai_codex:")

    def test_stream_deltas_reach_the_callback(self):
        chunks: list[str] = []

        async def on_delta(text: str) -> None:
            chunks.append(text)

        _run(
            OpenAICodexProvider(),
            _transport({}),
            stream=True,
            messages=[{"role": "user", "content": "hi"}],
            on_content_delta=on_delta,
        )
        assert chunks == ["hi"]

    def test_incomplete_stream_produces_no_resumable_state(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}, events=[{"type": "response.output_text.delta", "delta": "hi"}]),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.provider_state is None

    def test_state_round_trips_through_can_resume(self):
        provider = OpenAICodexProvider()
        response = _run(
            provider,
            _transport({}),
            messages=[{"role": "user", "content": "hi"}],
            model="openai-codex/gpt-5.6-sol",
        )

        assert response.provider_state is not None
        assert provider.can_resume_conversation_state(
            response.provider_state, "openai-codex/gpt-5.6-sol"
        )
        assert not provider.can_resume_conversation_state(
            response.provider_state, "openai-codex/gpt-5.6-luna"
        )

    def test_replayed_state_sends_prior_items(self):
        provider = OpenAICodexProvider()
        first = _run(
            provider,
            _transport({}),
            messages=[{"role": "user", "content": "hi"}],
            model="openai-codex/gpt-5.6-sol",
        )
        assert first.provider_state is not None

        captured: dict[str, Any] = {}
        _run(
            provider,
            _transport(captured),
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "again"},
            ],
            model="openai-codex/gpt-5.6-sol",
            provider_context=ProviderCallContext(
                conversation_state=first.provider_state.with_pending_messages(
                    [{"role": "user", "content": "again"}]
                ),
            ),
        )
        assert len(captured["body"]["input"]) > 1
        assert captured["body"]["reasoning"]["context"] == "all_turns"


# ======================================================================
# store=false item contract
# ======================================================================


class TestItemIdStripping:
    def test_ids_are_stripped_when_store_is_false(self):
        body = _without_response_item_ids({
            "store": False,
            "input": [{"id": "msg_1", "role": "user", "content": "hi"}],
        })
        assert body["input"] == [{"role": "user", "content": "hi"}]

    def test_ids_are_kept_when_store_is_true(self):
        original = {"store": True, "input": [{"id": "msg_1"}]}
        assert _without_response_item_ids(original) is original

    def test_non_dict_items_pass_through(self):
        body = _without_response_item_ids({"store": False, "input": ["raw", {"id": "x", "a": 1}]})
        assert body["input"] == ["raw", {"a": 1}]

    def test_missing_input_is_left_alone(self):
        original = {"store": False}
        assert _without_response_item_ids(original) is original

    def test_request_on_the_wire_carries_no_item_ids(self):
        provider = OpenAICodexProvider()
        first = _run(
            provider,
            _transport({}),
            messages=[{"role": "user", "content": "hi"}],
            model="openai-codex/gpt-5.6-sol",
        )
        assert first.provider_state is not None

        captured: dict[str, Any] = {}
        _run(
            provider,
            _transport(captured),
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": "b"}],
            model="openai-codex/gpt-5.6-sol",
            provider_context=ProviderCallContext(
                conversation_state=first.provider_state.with_pending_messages(
                    [{"role": "user", "content": "b"}]
                ),
            ),
        )
        assert all("id" not in item for item in captured["body"]["input"])


# ======================================================================
# Native compaction
# ======================================================================


def _state_with_context(model: str, tokens: int):
    state = build_responses_state(
        provider=f"openai_codex:{DEFAULT_CODEX_URL}",
        model=model,
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        output_items=[],
        usage={"total_tokens": tokens},
    )
    return state.with_pending_messages([{"role": "user", "content": "next"}])


_COMPACTION_EVENTS = [
    {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {"type": "compaction", "id": "cmp_1", "summary": "prior turns"},
    },
    {"type": "response.completed", "response": {"status": "completed"}},
]


class TestNativeCompaction:
    def test_enabled_by_default(self):
        assert OpenAICodexProvider().supports_native_compaction() is True

    def test_trigger_is_sent_then_compacted_input_is_reused(self):
        captured: dict[str, Any] = {}
        calls: list[list[dict[str, Any]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body["input"])
            events = _COMPACTION_EVENTS if len(calls) == 1 else _COMPLETED
            return httpx.Response(
                200,
                content=_sse_body(events),
                headers={"content-type": "text/event-stream"},
            )

        _ = captured
        provider = OpenAICodexProvider()
        response = _run(
            provider,
            httpx.MockTransport(handler),
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": "next"}],
            model="openai-codex/gpt-5.6-sol",
            max_tokens=1000,
            provider_context=ProviderCallContext(
                conversation_state=_state_with_context("gpt-5.6-sol", 100_000),
                context_window_tokens=100_000,
            ),
        )

        assert len(calls) == 2
        assert calls[0][-1] == {"type": "compaction_trigger"}
        assert any(item.get("type") == "compaction" for item in calls[1])
        assert response.content == "hi"

    def test_trigger_is_skipped_below_the_threshold(self):
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                content=_sse_body(_COMPLETED),
                headers={"content-type": "text/event-stream"},
            )

        _run(
            OpenAICodexProvider(),
            httpx.MockTransport(handler),
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": "next"}],
            model="openai-codex/gpt-5.6-sol",
            provider_context=ProviderCallContext(
                conversation_state=_state_with_context("gpt-5.6-sol", 10),
                context_window_tokens=100_000,
            ),
        )
        assert len(calls) == 1

    def test_incompatible_backend_disables_compaction_permanently(self):
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if body["input"] and body["input"][-1].get("type") == "compaction_trigger":
                return httpx.Response(
                    400,
                    content=b'{"error":{"message":"unknown compaction_trigger"}}',
                )
            return httpx.Response(
                200,
                content=_sse_body(_COMPLETED),
                headers={"content-type": "text/event-stream"},
            )

        provider = OpenAICodexProvider()
        response = _run(
            provider,
            httpx.MockTransport(handler),
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": "next"}],
            model="openai-codex/gpt-5.6-sol",
            provider_context=ProviderCallContext(
                conversation_state=_state_with_context("gpt-5.6-sol", 100_000),
                context_window_tokens=100_000,
            ),
        )

        # The turn still succeeds, and the attempt is not repeated.
        assert response.content == "hi"
        assert provider.supports_native_compaction() is False

    def test_missing_compaction_item_does_not_disable_the_feature(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=_sse_body(_COMPLETED),
                headers={"content-type": "text/event-stream"},
            )

        provider = OpenAICodexProvider()
        response = _run(
            provider,
            httpx.MockTransport(handler),
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": "next"}],
            model="openai-codex/gpt-5.6-sol",
            provider_context=ProviderCallContext(
                conversation_state=_state_with_context("gpt-5.6-sol", 100_000),
                context_window_tokens=100_000,
            ),
        )
        assert response.content == "hi"
        assert provider.supports_native_compaction() is True


class TestRetainedCompactionMessages:
    def test_keeps_only_conversational_roles(self):
        items = [
            {"role": "user", "content": "a"},
            {"type": "function_call", "name": "read_file"},
            {"role": "assistant", "content": "b"},
            {"role": "developer", "content": "c"},
        ]
        retained = _retained_compaction_messages(items)
        assert [item["role"] for item in retained] == ["user", "developer"]

    def test_preserves_original_order(self):
        items = [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}]
        assert _retained_compaction_messages(items) == items

    def test_always_keeps_the_most_recent_message(self):
        huge = {"role": "user", "content": "x" * 400_000}
        retained = _retained_compaction_messages([{"role": "user", "content": "old"}, huge])
        assert retained == [huge]

    def test_empty_input(self):
        assert _retained_compaction_messages([]) == []


# ======================================================================
# Error classification
# ======================================================================


class TestErrorClassification:
    def test_rate_limit_is_retryable_and_carries_retry_after(self):
        response = _run(
            OpenAICodexProvider(),
            _transport(
                {},
                status=429,
                headers={"retry-after": "7"},
                body=b'{"error":{"code":"rate_limit_exceeded"}}',
            ),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.finish_reason == "error"
        assert response.error_status_code == 429
        assert response.error_should_retry is True
        assert response.retry_after == 7

    def test_quota_exhaustion_is_not_retryable(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}, status=429, body=b'{"error":{"type":"insufficient_quota"}}'),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.error_should_retry is False

    def test_server_error_is_retryable(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}, status=503),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.error_status_code == 503
        assert response.error_should_retry is True

    def test_unauthorized_points_at_the_login_command(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}, status=401),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.error_status_code == 401
        assert response.error_should_retry is False
        assert "atom auth login openai-codex" in (response.content or "")

    def test_missing_credential_is_reported_as_auth_not_retried(self):
        with patch(
            "atom.providers.openai_codex_provider.get_codex_token",
            side_effect=RuntimeError("OAuth credentials not found. Please run the login command."),
        ):
            response = asyncio.run(
                OpenAICodexProvider().chat(messages=[{"role": "user", "content": "hi"}])
            )
        assert response.error_kind == "auth"
        assert response.error_should_retry is False
        assert "atom auth login openai-codex" in (response.content or "")

    def test_timeout_is_classified_as_transient(self):
        response = _codex_error_response(httpx.ReadTimeout("slow"))
        assert response.error_kind == "timeout"
        assert response.error_should_retry is True
        assert LLMProvider.is_transient_response(response) is True

    def test_connection_error_is_classified_as_transient(self):
        response = _codex_error_response(httpx.ConnectError("refused"))
        assert response.error_kind == "connection"
        assert response.error_should_retry is True

    def test_error_message_omits_the_upstream_payload(self):
        secret = "internal-trace-should-not-leak"
        response = _run(
            OpenAICodexProvider(),
            _transport({}, status=500, body=secret.encode()),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert secret not in (response.content or "")

    def test_log_summary_is_bounded_to_status_and_codes(self):
        response = _codex_error_response(
            _CodexHTTPError(
                "HTTP 400: Codex API request failed",
                status_code=400,
                error_type="invalid_request_error",
                error_code="bad_param",
            )
        )
        summary = _codex_log_summary("CodexHTTPError", response)
        assert summary == "HTTP 400 type=invalid_request_error code=bad_param"

    def test_log_summary_falls_back_to_kind(self):
        summary = _codex_log_summary("ReadTimeout", _codex_error_response(httpx.ReadTimeout("x")))
        assert summary == "ReadTimeout timeout"

    def test_no_token_material_appears_in_error_output(self):
        response = _run(
            OpenAICodexProvider(),
            _transport({}, status=500),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert _TOKEN not in (response.content or "")


class TestShouldRetryStatus:
    @pytest.mark.parametrize("status", [408, 409, 500, 502, 503])
    def test_retryable_statuses(self, status: int):
        assert _should_retry_status(status, None, None, None) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_non_retryable_statuses(self, status: int):
        assert _should_retry_status(status, None, None, None) is False


# ======================================================================
# Small helpers
# ======================================================================


class TestStripModelPrefix:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("openai-codex/gpt-5.6-sol", "gpt-5.6-sol"),
            ("openai_codex/gpt-5.6-sol", "gpt-5.6-sol"),
            ("gpt-5.6-sol", "gpt-5.6-sol"),
            ("openai/gpt-4o", "openai/gpt-4o"),
        ],
    )
    def test_prefix_handling(self, given: str, expected: str):
        assert strip_model_prefix(given) == expected


class TestReasoningOptions:
    def test_default_requests_visible_summaries_without_forcing_effort(self):
        assert _build_reasoning_options(None) == {"summary": "auto"}

    def test_effort_is_passed_through(self):
        assert _build_reasoning_options("high") == {"summary": "auto", "effort": "high"}

    def test_none_disables_reasoning_entirely(self):
        assert _build_reasoning_options("none") == {"effort": "none"}
        assert _build_reasoning_options("NONE") == {"effort": "none"}


class TestPromptCacheKey:
    def test_is_stable_for_equal_input(self):
        messages = [{"role": "user", "content": "hi"}]
        assert _prompt_cache_key(messages) == _prompt_cache_key(messages)

    def test_differs_across_conversations(self):
        assert _prompt_cache_key([{"role": "user", "content": "a"}]) != _prompt_cache_key(
            [{"role": "user", "content": "b"}]
        )

    def test_handles_unserializable_values(self):
        assert _prompt_cache_key([{"role": "user", "content": object()}])


class TestProviderMetadata:
    def test_default_model_is_reported(self):
        assert OpenAICodexProvider().get_default_model() == "openai-codex/gpt-5.6-sol"

    def test_progress_deltas_are_supported(self):
        assert OpenAICodexProvider().supports_progress_deltas is True

    def test_no_api_key_is_held(self):
        provider = OpenAICodexProvider()
        assert provider.api_key is None
        assert provider.api_base is None

    def test_foreign_state_is_rejected(self):
        state = build_responses_state(
            provider="openai_compat:https://api.openai.com/v1",
            model="gpt-5.6-sol",
            input_items=[{"role": "user", "content": "hi"}],
            output_items=[],
        )
        assert OpenAICodexProvider().can_resume_conversation_state(state) is False
