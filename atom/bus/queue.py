"""Async message queue for decoupled channel-agent communication."""

import asyncio
import dataclasses
from typing import Protocol

from atom.bus.events import InboundMessage, OutboundMessage
from atom.bus.outbound_events import (
    StreamDeltaEvent,
    replace_event_content,
    replace_outbound_event,
)


class OutboundTextFilter(Protocol):
    """Rewrites user-visible outbound text.

    Stateful across calls: a streamed message arrives as many deltas, so a
    filter may need to hold back a partial match and release it later. ``final``
    tells it no more text is coming for *stream_id*.
    """

    def __call__(
        self, text: str, *, stream_id: str | None = None, final: bool = False,
    ) -> str:
        ...


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        #: Applied to outbound text on its way to a channel. Set by the agent
        #: loop to resolve privacy placeholders, so every consumer — channel
        #: manager, CLI, SDK — is covered by one hook rather than each having to
        #: remember. Left None when tokenization is off.
        self.outbound_text_filter: OutboundTextFilter | None = None

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(self.filter_outbound(msg))

    def filter_outbound(self, msg: OutboundMessage) -> OutboundMessage:
        """Apply :attr:`outbound_text_filter` to a message's user-visible text.

        Typed events carry their own copy of the content, so both must be
        rewritten or a channel reading the event would bypass the filter.

        A stream delta is passed with its ``stream_id`` and is not final, so the
        filter can hold back a partial match until the next delta. Everything
        else is a complete message and is filtered as final.
        """
        transform = self.outbound_text_filter
        if transform is None:
            return msg
        event = msg.event
        is_delta = isinstance(event, StreamDeltaEvent)
        if not msg.content and not is_delta:
            return msg
        stream_id = getattr(event, "stream_id", None) if event is not None else None
        content = transform(msg.content, stream_id=stream_id, final=not is_delta)
        if content == msg.content:
            return msg
        if msg.event is not None:
            return replace_outbound_event(
                msg,
                replace_event_content(msg.event, content),
                content=content,
            )
        return dataclasses.replace(msg, content=content)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
