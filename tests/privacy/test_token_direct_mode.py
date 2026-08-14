"""Placeholder resolution on the direct path (``process_direct``).

``process_direct`` returns its result to the caller and invokes stream/progress
callbacks itself, so none of it passes through ``publish_outbound``. Each of
these was a real bug found by running the agent, not by unit tests:

* the returned message showed a raw placeholder
* streamed deltas showed a raw placeholder
* a tool-hint progress line showed a raw placeholder while the reply beside it
  showed the value
"""

from __future__ import annotations

import pytest

from atom.agent.loop import AgentLoop
from atom.bus.events import OutboundMessage
from atom.bus.queue import MessageBus
from atom.bus.runtime_events import RuntimeEventPublisher
from atom.privacy import tokens as tokens_module
from atom.privacy.stream import PlaceholderStreamResolver
from atom.privacy.tokens import TokenStore, tokenize

EMAIL = "alex@example.com"


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


@pytest.fixture
def loop(store: TokenStore) -> AgentLoop:
    """A loop with only what the direct path touches, as the API tests do."""
    instance = AgentLoop.__new__(AgentLoop)
    instance._session_locks = {}
    instance.runtime_event_publisher = RuntimeEventPublisher()
    instance.bus = MessageBus()
    instance.bus.outbound_text_filter = PlaceholderStreamResolver()
    return instance


class TestReturnedMessage:
    def test_returned_content_is_resolved(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        token = tokenize(EMAIL)
        msg = OutboundMessage(channel="cli", chat_id="direct", content=f"see {token}")
        resolved = loop._resolve_outbound_placeholders(msg)
        assert resolved is not None
        assert resolved.content == f"see {EMAIL}"

    def test_none_is_passed_through(self, loop: AgentLoop) -> None:
        assert loop._resolve_outbound_placeholders(None) is None

    def test_message_without_placeholders_is_unchanged(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        msg = OutboundMessage(channel="cli", chat_id="direct", content="plain")
        assert loop._resolve_outbound_placeholders(msg) is msg


class TestStreamCallbacks:
    async def test_deltas_are_resolved(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        token = tokenize(EMAIL)
        seen: list[str] = []

        async def on_stream(delta: str) -> None:
            seen.append(delta)

        wrapped, wrapped_end = loop._resolve_direct_stream_callbacks(
            "direct:cli:1", on_stream, None,
        )
        assert wrapped is not None and wrapped_end is not None
        for chunk in (token[:6], token[6:]):
            await wrapped(chunk)
        await wrapped_end()
        assert "".join(seen) == EMAIL

    async def test_held_tail_is_released_at_stream_end(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        """An incomplete placeholder must still reach the user, not vanish."""
        seen: list[str] = []

        async def on_stream(delta: str) -> None:
            seen.append(delta)

        wrapped, wrapped_end = loop._resolve_direct_stream_callbacks(
            "direct:cli:2", on_stream, None,
        )
        assert wrapped is not None and wrapped_end is not None
        await wrapped("tail «incomplete")
        await wrapped_end()
        assert "".join(seen) == "tail «incomplete"

    async def test_inner_stream_end_still_runs(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        calls: list[dict[str, object]] = []

        async def on_stream(_delta: str) -> None:
            pass

        async def on_stream_end(**kwargs: object) -> None:
            calls.append(kwargs)

        wrapped, wrapped_end = loop._resolve_direct_stream_callbacks(
            "direct:cli:3", on_stream, on_stream_end,
        )
        assert wrapped_end is not None
        await wrapped_end(resuming=True)
        assert calls == [{"resuming": True}]

    def test_callbacks_pass_through_without_a_filter(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        loop.bus.outbound_text_filter = None

        async def on_stream(_delta: str) -> None:
            pass

        wrapped, wrapped_end = loop._resolve_direct_stream_callbacks(
            "direct:cli:4", on_stream, None,
        )
        assert wrapped is on_stream
        assert wrapped_end is None


class TestProgressCallback:
    async def test_progress_lines_are_resolved(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        token = tokenize(EMAIL)
        seen: list[str] = []

        async def on_progress(content: str = "", **_kwargs: object) -> None:
            seen.append(content)

        wrapped = loop._resolve_direct_progress_callback(on_progress)
        assert wrapped is not None
        await wrapped(f"$ echo '{token}'", tool_hint=True)
        assert seen == [f"$ echo '{EMAIL}'"]

    async def test_keyword_arguments_survive(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        received: list[dict[str, object]] = []

        async def on_progress(content: str = "", **kwargs: object) -> None:
            received.append({"content": content, **kwargs})

        wrapped = loop._resolve_direct_progress_callback(on_progress)
        assert wrapped is not None
        await wrapped("hello", tool_hint=True, reasoning=False)
        assert received == [
            {"content": "hello", "tool_hint": True, "reasoning": False}
        ]

    async def test_progress_does_not_hold_partial_text(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        """Progress lines are self-contained; holding one would drop it."""
        seen: list[str] = []

        async def on_progress(content: str = "", **_kwargs: object) -> None:
            seen.append(content)

        wrapped = loop._resolve_direct_progress_callback(on_progress)
        assert wrapped is not None
        await wrapped("$ grep «partial")
        assert seen == ["$ grep «partial"]

    def test_passes_through_without_a_filter(
        self, loop: AgentLoop, store: TokenStore,
    ) -> None:
        loop.bus.outbound_text_filter = None

        async def on_progress(content: str = "", **_kwargs: object) -> None:
            pass

        assert loop._resolve_direct_progress_callback(on_progress) is on_progress

    def test_none_progress_is_tolerated(self, loop: AgentLoop) -> None:
        assert loop._resolve_direct_progress_callback(None) is None
