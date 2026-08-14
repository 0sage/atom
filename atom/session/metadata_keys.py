"""Legacy message metadata keys that still appear in persisted session history.

The WebUI and its ``websocket`` transport are gone, but these key strings were
written into on-disk history and cron/trigger origin metadata by earlier
versions. They are kept here (with their exact original values) so surviving
code can recognise and strip them instead of carrying stale values into new
turns.
"""

from __future__ import annotations

WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"

__all__ = ["WEBUI_TURN_METADATA_KEY", "WEBUI_MESSAGE_SOURCE_METADATA_KEY"]
