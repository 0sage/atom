"""Message bus module for decoupled channel-agent communication."""

from atom.bus.events import InboundMessage, OutboundMessage
from atom.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
