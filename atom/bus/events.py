"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atom.bus.outbound_events import OutboundEvent

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Optional ``OutboundMessage.metadata`` key asking the channel to delete the
# user message this reply answers, identified by ``metadata["message_id"]``.
# Set only when the user's own text is the thing that must not persist — a
# secret value typed into a chat. Channels that cannot delete ignore it, so the
# request is best-effort by construction and callers must not report success.
OUTBOUND_META_DELETE_SOURCE = "_delete_source_message"

# Internal-only inbound metadata used by in-process channels to ask the agent
# loop to update runtime state without going through a user session.
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
RUNTIME_CONTROL_ACK = "_ack"
RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD = "image_generation_reload"
RUNTIME_CONTROL_SESSION_DISCARD = "session_discard"


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    require_existing_session: bool = False

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``event`` carries internal runtime/UI semantics. ``metadata`` is reserved
    for channel routing context (``message_id``, thread ids, etc.) and optional
    ``OUTBOUND_META_AGENT_UI`` blobs for rich clients.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
    event: "OutboundEvent | None" = None
