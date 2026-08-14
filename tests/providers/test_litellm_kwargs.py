"""Tests for OpenAICompatProvider spec-driven behavior.

Validates that:
- OpenRouter (no strip) keeps model names intact.
- AiHubMix (strip_model_prefix=True) strips provider prefixes.
- Standard providers pass model names through as-is.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atom.providers.base import ProviderCallContext
from atom.providers.openai_compat_provider import OpenAICompatProvider
from atom.providers.registry import find_by_name


def _fake_chat_response(content: str = "ok") -> SimpleNamespace:
    """Build a minimal OpenAI chat completion response."""
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


def _fake_tool_call_response() -> SimpleNamespace:
    """Build a minimal chat response that includes provider extra_content."""
    function = SimpleNamespace(
        name="exec",
        arguments='{"cmd":"ls"}',
        provider_specific_fields={"inner": "value"},
    )
    tool_call = SimpleNamespace(
        id="call_123",
        index=0,
        type="function",
        function=function,
        extra_content={"google": {"thought_signature": "signed-token"}},
    )
    message = SimpleNamespace(
        content=None,
        tool_calls=[tool_call],
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


def _fake_tool_call_response_with_arguments(arguments) -> SimpleNamespace:
    """Build a minimal chat response with caller-supplied tool arguments."""
    function = SimpleNamespace(name="optional_tool", arguments=arguments)
    tool_call = SimpleNamespace(id="call_123", type="function", function=function)
    message = SimpleNamespace(content=None, tool_calls=[tool_call], reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    return SimpleNamespace(choices=[choice], usage=SimpleNamespace())


def _fake_responses_response(content: str = "ok") -> MagicMock:
    """Build a minimal Responses API response object."""
    resp = MagicMock()
    resp.model_dump.return_value = {
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
        }],
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    return resp


def _fake_responses_stream(text: str = "ok"):
    async def _stream():
        yield SimpleNamespace(type="response.output_text.delta", delta=text)
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
                output=[],
            ),
        )

    return _stream()


def _fake_chat_stream(text: str = "ok"):
    async def _stream():
        yield SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content=text, reasoning_content=None, tool_calls=None))],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    return _stream()


def _fake_chat_stream_tool_call_chunks():
    """Mimic OpenAI-compatible streaming tool-call argument deltas."""

    async def _stream():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_write",
                                function=SimpleNamespace(
                                    name="write_file",
                                    arguments='{"path":"notes.md","content":"',
                                ),
                            )
                        ],
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name=None, arguments='line\\n"}'),
                            )
                        ],
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=None,
                    ),
                ),
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    return _stream()


def _fake_chat_stream_legacy_function_call_chunks():
    """Mimic older OpenAI-compatible ``delta.function_call`` chunks."""

    async def _stream():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=None,
                        function_call=SimpleNamespace(
                            name="write_file",
                            arguments='{"path":"notes.md","content":"',
                        ),
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=None,
                        function_call=SimpleNamespace(
                            name=None,
                            arguments='line\\n"}',
                        ),
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="function_call",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        reasoning=None,
                        tool_calls=None,
                        function_call=None,
                    ),
                ),
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    return _stream()


def _fake_chat_stream_reasoning_chunks():
    """Mimic a ``chat.completions`` stream: ``reasoning_content`` then ``content``."""

    async def _stream():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="step1",
                        reasoning=None,
                        tool_calls=None,
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="step2",
                        reasoning=None,
                        tool_calls=None,
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content="answer",
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                ),
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                ),
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )

    return _stream()


@pytest.mark.asyncio
async def test_openai_compat_stream_forwards_reasoning_content_deltas() -> None:
    """Reasoning models expose ``delta.reasoning_content`` during streaming."""
    mock_chat = AsyncMock(return_value=_fake_chat_stream_reasoning_chunks())
    spec = find_by_name("groq")
    thinking: list[str] = []
    content: list[str] = []

    async def on_thinking(d: str) -> None:
        thinking.append(d)

    async def on_content(d: str) -> None:
        content.append(d)

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_openai:
        client_instance = mock_openai.return_value
        client_instance.chat.completions.create = mock_chat

        provider = OpenAICompatProvider(
            api_key="sk-test",
            default_model="reasoning-model",
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="reasoning-model",
            reasoning_effort="high",
            on_content_delta=on_content,
            on_thinking_delta=on_thinking,
        )

    assert thinking == ["step1", "step2"]
    assert content == ["answer"]
    assert result.reasoning_content == "step1step2"
    assert result.content == "answer"
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "model"),
    [
        ("openai", "gpt-4o"),
        ("groq", "llama-3.3-70b-versatile"),
    ],
)
async def test_openai_compat_stream_forwards_tool_call_argument_deltas(
    provider_name: str,
    model: str,
) -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_stream_tool_call_chunks())
    spec = find_by_name(provider_name)
    deltas: list[dict] = []

    async def on_tool_delta(delta: dict) -> None:
        deltas.append(delta)

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_openai:
        client_instance = mock_openai.return_value
        client_instance.chat.completions.create = mock_chat

        provider = OpenAICompatProvider(
            api_key="sk-test",
            default_model=model,
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "write"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            model=model,
            on_tool_call_delta=on_tool_delta,
        )

    assert deltas == [
        {
            "index": 0,
            "call_id": "call_write",
            "name": "write_file",
            "arguments_delta": '{"path":"notes.md","content":"',
        },
        {"index": 0, "call_id": "", "name": "", "arguments_delta": 'line\\n"}'},
    ]
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments == {"path": "notes.md", "content": "line\n"}
    kwargs = mock_chat.await_args.kwargs
    assert kwargs.get("extra_body", {}).get("tool_stream") is None


@pytest.mark.asyncio
async def test_openai_compat_stream_forwards_legacy_function_call_argument_deltas() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_stream_legacy_function_call_chunks())
    deltas: list[dict] = []

    async def on_tool_delta(delta: dict) -> None:
        deltas.append(delta)

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_openai:
        client_instance = mock_openai.return_value
        client_instance.chat.completions.create = mock_chat

        provider = OpenAICompatProvider(
            api_key="sk-test",
            default_model="deepseek-chat",
            spec=find_by_name("deepseek"),
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "write"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            model="deepseek-chat",
            on_tool_call_delta=on_tool_delta,
        )

    assert deltas == [
        {
            "index": 0,
            "call_id": "",
            "name": "write_file",
            "arguments_delta": '{"path":"notes.md","content":"',
        },
        {"index": 0, "call_id": "", "name": "", "arguments_delta": 'line\\n"}'},
    ]
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments == {"path": "notes.md", "content": "line\n"}


class _FakeResponsesError(Exception):
    def __init__(self, status_code: int, text: str):
        super().__init__(text)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, text=text, headers={})


class _StalledStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_standard_provider_passes_model_through() -> None:
    """A standard provider passes the model name through as-is."""
    mock_create = AsyncMock(return_value=_fake_chat_response())
    spec = find_by_name("groq")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="deepseek-chat",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_openai_compat_preserves_extra_content_on_tool_calls() -> None:
    """Provider extra_content (thought signatures) must survive parse→serialize round-trip."""
    mock_create = AsyncMock(return_value=_fake_tool_call_response())
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="test-key",
            api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
            default_model="google/gemini-3.1-pro-preview",
            spec=spec,
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "run exec"}],
            model="google/gemini-3.1-pro-preview",
        )

    assert len(result.tool_calls) == 1
    tool_call = result.tool_calls[0]
    assert tool_call.id == "call_123"
    assert tool_call.extra_content == {"google": {"thought_signature": "signed-token"}}
    assert tool_call.function_provider_specific_fields == {"inner": "value"}

    serialized = tool_call.to_openai_tool_call()
    assert serialized["extra_content"] == {"google": {"thought_signature": "signed-token"}}
    assert serialized["function"]["provider_specific_fields"] == {"inner": "value"}


def test_openai_compat_parse_preserves_malformed_tool_arguments() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    result = provider._parse(_fake_tool_call_response_with_arguments('{path:"foo.txt"}'))

    assert result.tool_calls[0].arguments == '{path:"foo.txt"}'


def test_openai_compat_parse_preserves_array_tool_arguments() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    result = provider._parse(_fake_tool_call_response_with_arguments('["foo.txt"]'))

    assert result.tool_calls[0].arguments == ["foo.txt"]


def test_openai_model_passthrough() -> None:
    """OpenAI models pass through unchanged."""
    spec = find_by_name("openai")
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-4o",
            spec=spec,
        )
    assert provider.get_default_model() == "gpt-4o"


@pytest.mark.asyncio
async def test_direct_openai_gpt5_uses_responses_api() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response())
    mock_responses = AsyncMock(return_value=_fake_responses_response("from responses"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.content == "from responses"
    mock_responses.assert_awaited_once()
    mock_chat.assert_not_awaited()
    call_kwargs = mock_responses.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5-chat"
    assert call_kwargs["max_output_tokens"] == 4096
    assert "input" in call_kwargs
    assert "messages" not in call_kwargs
    assert call_kwargs["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
async def test_direct_openai_reasoning_prefers_responses_api() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response())
    mock_responses = AsyncMock(return_value=_fake_responses_response("reasoned"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-4o",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o",
            reasoning_effort="medium",
        )

    mock_responses.assert_awaited_once()
    mock_chat.assert_not_awaited()
    call_kwargs = mock_responses.call_args.kwargs
    assert call_kwargs["reasoning"] == {"effort": "medium"}
    assert call_kwargs["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
async def test_direct_openai_retries_without_unsupported_server_compaction() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response())
    mock_responses = AsyncMock(side_effect=[
        _FakeResponsesError(400, "Unknown parameter: context_management"),
        _fake_responses_response("compaction fallback"),
    ])
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5.6",
            spec=spec,
        )

        result = await provider.chat_with_context(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5.6",
            provider_context=ProviderCallContext(context_window_tokens=200_000),
        )

    assert result.content == "compaction fallback"
    assert result.provider_state is not None
    assert mock_responses.await_count == 2
    assert "context_management" in mock_responses.call_args_list[0].kwargs
    assert "context_management" not in mock_responses.call_args_list[1].kwargs
    assert provider.supports_native_compaction("gpt-5.6") is False
    mock_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_openai_gpt4o_stays_on_chat_completions() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response())
    mock_responses = AsyncMock(return_value=_fake_responses_response())
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-4o",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o",
        )

    mock_chat.assert_awaited_once()
    mock_responses.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_openai_streaming_gpt5_uses_responses_api() -> None:
    mock_chat = AsyncMock(return_value=_StalledStream())
    mock_responses = AsyncMock(return_value=_fake_responses_stream("hi"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.content == "hi"
    assert result.finish_reason == "stop"
    mock_responses.assert_awaited_once()
    mock_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_openai_responses_404_falls_back_to_chat_completions() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response("from chat"))
    mock_responses = AsyncMock(side_effect=_FakeResponsesError(404, "Responses endpoint not supported"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.content == "from chat"
    mock_responses.assert_awaited_once()
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_openai_open_circuit_skips_responses_api() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response("from chat"))
    mock_responses = AsyncMock(return_value=_fake_responses_response("from responses"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        for _ in range(3):
            provider._record_responses_failure("gpt-5-chat", None)

        result = await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.content == "from chat"
    mock_responses.assert_not_awaited()
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_openai_stream_responses_unsupported_param_falls_back() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_stream("fallback stream"))
    mock_responses = AsyncMock(
        side_effect=_FakeResponsesError(400, "Unknown parameter: max_output_tokens for Responses API")
    )
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.content == "fallback stream"
    mock_responses.assert_awaited_once()
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_openai_responses_rate_limit_does_not_fallback() -> None:
    mock_chat = AsyncMock(return_value=_fake_chat_response("from chat"))
    mock_responses = AsyncMock(side_effect=_FakeResponsesError(429, "rate limit"))
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_chat
        client_instance.responses.create = mock_responses

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-chat",
        )

    assert result.finish_reason == "error"
    mock_responses.assert_awaited_once()
    mock_chat.assert_not_awaited()


def test_openai_compat_supports_temperature_matches_reasoning_model_rules() -> None:
    assert OpenAICompatProvider._supports_temperature("gpt-4o") is True
    assert OpenAICompatProvider._supports_temperature("gpt-5-chat") is False
    assert OpenAICompatProvider._supports_temperature("o3-mini") is False
    assert OpenAICompatProvider._supports_temperature("gpt-4o", reasoning_effort="medium") is False


def test_openai_compat_build_kwargs_uses_gpt5_safe_parameters() -> None:
    spec = find_by_name("openai")
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-5-chat",
            spec=spec,
        )

    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model="gpt-5-chat",
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["model"] == "gpt-5-chat"
    assert kwargs["max_completion_tokens"] == 4096
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.parametrize(
    ("model_name", "expected_key"),
    [
        ("gpt-5.4", "max_completion_tokens"),
        ("o1-mini", "max_completion_tokens"),
        ("o3-mini", "max_completion_tokens"),
        ("o4-mini", "max_completion_tokens"),
        ("gpt-4", "max_tokens"),
        ("foo3-mini", "max_tokens"),
        ("foo4-mini", "max_tokens"),
    ],
)
def test_openai_compat_build_kwargs_max_completion_tokens_by_model_name(
    model_name: str,
    expected_key: str,
) -> None:
    spec = find_by_name("custom")
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model=model_name,
            spec=spec,
        )

    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model=model_name,
        max_tokens=2048,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    )

    other_key = (
        "max_tokens" if expected_key == "max_completion_tokens" else "max_completion_tokens"
    )
    assert kwargs[expected_key] == 2048
    assert other_key not in kwargs


def test_openai_compat_preserves_message_level_reasoning_fields() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "done",
            "reasoning_content": "hidden",
            "extra_content": {"debug": True},
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "fn", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig"}},
                }
            ],
        },
        {"role": "user", "content": "thanks"},
    ])

    assert sanitized[1]["content"] is None
    assert sanitized[1]["reasoning_content"] == "hidden"
    assert sanitized[1]["extra_content"] == {"debug": True}
    assert sanitized[1]["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig"}}


def _tool_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "my", "arguments": "{}"},
    }


def test_openai_compat_preserves_tool_call_ids_after_consecutive_assistant_messages() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "不错"},
        {"role": "assistant", "content": "对，破 4 万指日可待"},
        {
            "role": "assistant",
            "content": "<think>我再查一下</think>",
            "tool_calls": [
                {
                    "id": "call_function_akxp3wqzn7ph_1",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_function_akxp3wqzn7ph_1", "name": "exec", "content": "ok"},
        {"role": "user", "content": "多少star了呢"},
    ])

    assert sanitized[1]["role"] == "assistant"
    assert sanitized[1]["content"] is None
    assert sanitized[1]["tool_calls"][0]["id"] == "call_function_akxp3wqzn7ph_1"
    assert sanitized[2]["tool_call_id"] == "call_function_akxp3wqzn7ph_1"


def test_openai_compat_deduplicates_duplicate_tool_call_ids_in_history() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "check both files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "ab1b45c2a",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                },
                {
                    "id": "ab1b45c2a",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"b.txt"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "ab1b45c2a", "name": "read_file", "content": "a"},
        {"role": "tool", "tool_call_id": "ab1b45c2a", "name": "read_file", "content": "b"},
        {"role": "user", "content": "continue"},
    ])

    tool_call_ids = [tc["id"] for tc in sanitized[1]["tool_calls"]]
    tool_result_ids = [sanitized[2]["tool_call_id"], sanitized[3]["tool_call_id"]]

    assert tool_call_ids[0] == "ab1b45c2a"
    assert len(tool_call_ids) == len(set(tool_call_ids)) == 2
    assert tool_result_ids == tool_call_ids


def test_openai_compat_stringifies_dict_tool_arguments() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "exec", "arguments": {"cmd": "ls -la"}},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "ok"},
        {"role": "user", "content": "done"},
    ])

    assert sanitized[1]["tool_calls"][0]["function"]["arguments"] == '{"cmd": "ls -la"}'


def test_openai_compat_repairs_object_like_history_tool_arguments_string() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{'cmd': 'pwd'}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "ok"},
        {"role": "user", "content": "done"},
    ])

    assert sanitized[1]["tool_calls"][0]["function"]["arguments"] == '{"cmd": "pwd"}'


def test_openai_compat_defaults_missing_tool_arguments_to_empty_object() -> None:
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    sanitized = provider._sanitize_messages([
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "exec"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "ok"},
        {"role": "user", "content": "done"},
    ])

    assert sanitized[1]["tool_calls"][0]["function"]["arguments"] == "{}"


@pytest.mark.asyncio
async def test_openai_compat_stream_watchdog_returns_error_on_stall(monkeypatch) -> None:
    monkeypatch.setenv("ATOM_STREAM_IDLE_TIMEOUT_S", "0.01")
    mock_create = AsyncMock(return_value=_StalledStream())
    spec = find_by_name("openai")

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_client_class:
        client_instance = mock_client_class.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-4o",
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o",
        )

    assert result.finish_reason == "error"
    assert result.content is not None
    assert "stream stalled" in result.content


# ---------------------------------------------------------------------------
# Provider-specific thinking parameters (extra_body)
# ---------------------------------------------------------------------------

def _build_kwargs_for(provider_name: str, model: str, reasoning_effort=None):
    spec = find_by_name(provider_name)
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        p = OpenAICompatProvider(api_key="k", default_model=model, spec=spec)
    return p._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model=model, max_tokens=1024, temperature=0.7,
        reasoning_effort=reasoning_effort, tool_choice=None,
    )


def test_openai_compat_keeps_list_content() -> None:
    """OpenAI-compatible providers keep structured content blocks intact."""
    spec = find_by_name("openai")
    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        p = OpenAICompatProvider(api_key="k", default_model="gpt-4o", spec=spec)

    kw = p._build_kwargs(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
            ],
        }],
        tools=None,
        model="gpt-4o",
        max_tokens=1024,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert isinstance(kw["messages"][0]["content"], list)


def test_openai_no_thinking_extra_body() -> None:
    """Non-thinking providers should never get extra_body for thinking."""
    kw = _build_kwargs_for("openai", "gpt-4o", reasoning_effort="medium")
    assert "extra_body" not in kw


# ---------------------------------------------------------------------------
# reasoning_effort="none" — treated as thinking disabled
# ---------------------------------------------------------------------------


