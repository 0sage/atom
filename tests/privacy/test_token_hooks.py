"""The two tokenization boundaries: ingress on user text, egress on the bus.

Between them everything carries placeholders. These tests pin the boundary
itself, not the substitution — that lives in test_tokens.py.
"""

from __future__ import annotations

import pytest

from atom.bus.events import OutboundMessage
from atom.bus.outbound_events import ProgressEvent, StreamDeltaEvent
from atom.bus.queue import MessageBus
from atom.privacy import tokens as tokens_module
from atom.privacy.hooks import tokenize_user_text
from atom.privacy.stream import PlaceholderStreamResolver
from atom.privacy.tokens import TokenStore, tokenize

EMAIL = "alex@example.com"


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


class TestIngress:
    def test_tokenizes_when_enabled(self, store: TokenStore) -> None:
        out = tokenize_user_text(f"mail {EMAIL}", enabled=True)
        assert EMAIL not in out
        assert "«email:" in out

    def test_passthrough_when_disabled(self, store: TokenStore) -> None:
        text = f"mail {EMAIL}"
        assert tokenize_user_text(text, enabled=False) == text
        assert len(store) == 0

    @pytest.mark.parametrize(
        "command",
        [
            "/secrets set TOKEN=a@b.com",
            "  /secrets set TOKEN=a@b.com",
            "/help",
        ],
    )
    def test_slash_commands_are_untouched(
        self, store: TokenStore, command: str,
    ) -> None:
        """Command arguments go to a handler, not a model. /secrets in
        particular must reach the store byte-for-byte."""
        assert tokenize_user_text(command, enabled=True) == command
        assert len(store) == 0

    def test_text_merely_containing_a_slash_is_tokenized(
        self, store: TokenStore,
    ) -> None:
        out = tokenize_user_text(f"see a/b and {EMAIL}", enabled=True)
        assert EMAIL not in out

    def test_empty_text(self, store: TokenStore) -> None:
        assert tokenize_user_text("", enabled=True) == ""


class TestEgress:
    """One filter on the transport, so every consumer is covered."""

    @pytest.fixture
    def bus(self, store: TokenStore) -> MessageBus:
        b = MessageBus()
        b.outbound_text_filter = PlaceholderStreamResolver()
        return b

    async def test_resolves_content(self, bus: MessageBus, store: TokenStore) -> None:
        token = tokenize(EMAIL)
        await bus.publish_outbound(
            OutboundMessage(channel="telegram", chat_id="1", content=f"mail {token}")
        )
        assert (await bus.consume_outbound()).content == f"mail {EMAIL}"

    async def test_resolves_stream_delta_event_content(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        """Streaming carries its own copy of the text; a channel reading the
        event must not bypass the filter."""
        token = tokenize(EMAIL)
        await bus.publish_outbound(
            OutboundMessage(
                channel="telegram", chat_id="1", content=token,
                event=StreamDeltaEvent(content=token, stream_id="s1"),
            )
        )
        received = await bus.consume_outbound()
        assert received.content == EMAIL
        assert isinstance(received.event, StreamDeltaEvent)
        assert received.event.content == EMAIL
        assert received.event.stream_id == "s1", "other event fields must survive"

    async def test_resolves_progress_event_content(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        token = tokenize(EMAIL)
        await bus.publish_outbound(
            OutboundMessage(
                channel="cli", chat_id="1", content=token,
                event=ProgressEvent(content=token, tool_hint=True),
            )
        )
        received = await bus.consume_outbound()
        assert isinstance(received.event, ProgressEvent)
        assert received.event.content == EMAIL
        assert received.event.tool_hint is True

    async def test_no_filter_leaves_text_alone(self, store: TokenStore) -> None:
        plain = MessageBus()
        token = tokenize(EMAIL)
        await plain.publish_outbound(
            OutboundMessage(channel="cli", chat_id="1", content=token)
        )
        assert (await plain.consume_outbound()).content == token

    async def test_message_without_tokens_is_returned_unchanged(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        msg = OutboundMessage(channel="cli", chat_id="1", content="plain text")
        await bus.publish_outbound(msg)
        assert (await bus.consume_outbound()) is msg

    async def test_empty_content_is_safe(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        await bus.publish_outbound(
            OutboundMessage(channel="cli", chat_id="1", content="")
        )
        assert (await bus.consume_outbound()).content == ""

    async def test_unknown_token_reaches_the_user_as_is(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        await bus.publish_outbound(
            OutboundMessage(
                channel="cli", chat_id="1", content="see «email:deadbeef»",
            )
        )
        assert (await bus.consume_outbound()).content == "see «email:deadbeef»"

    async def test_inbound_is_not_filtered(
        self, bus: MessageBus, store: TokenStore,
    ) -> None:
        """The filter is egress-only; ingress tokenizes instead."""
        from atom.bus.events import InboundMessage

        await bus.publish_inbound(
            InboundMessage(
                channel="cli", sender_id="u", chat_id="1", content=tokenize(EMAIL),
            )
        )
        assert "«email:" in (await bus.consume_inbound()).content


class TestRuntimeContext:
    """The model must be told what a placeholder is, or it asks for data twice."""

    async def test_block_explains_placeholders(self, store: TokenStore) -> None:
        from atom.agent.tools.context import RequestContext
        from atom.privacy.hooks import provide_token_runtime_context

        block = await provide_token_runtime_context(
            RequestContext(channel="cli", chat_id="1")
        )
        assert block is not None
        assert block.source == "privacy_tokens"
        assert "«email:" in block.content
        # The three behaviours the guidance exists to prevent.
        assert "never invent" in block.content
        assert "do not ask the user" in block.content
        assert "sees the real value" in block.content

    def test_hint_is_a_literal_example_not_a_regex(self) -> None:
        """Models follow a concrete example more reliably than a pattern."""
        from atom.privacy.tokens import TOKEN_PATTERN_HINT

        assert TOKEN_PATTERN_HINT == "«email:a91f2c8d»"


class TestRoundTripThroughBothHooks:
    async def test_user_sees_the_real_address_history_does_not(
        self, store: TokenStore,
    ) -> None:
        ingress = tokenize_user_text(f"mail {EMAIL}", enabled=True)
        assert EMAIL not in ingress, "history and the provider see a placeholder"

        bus = MessageBus()
        bus.outbound_text_filter = PlaceholderStreamResolver()
        await bus.publish_outbound(
            OutboundMessage(channel="telegram", chat_id="1", content=ingress)
        )
        assert EMAIL in (await bus.consume_outbound()).content
