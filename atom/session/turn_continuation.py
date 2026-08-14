"""Turn persistence boundary helpers.

This module keeps history-append bookkeeping out of ``AgentLoop``. The loop asks
whether the triggering user message should be persisted, and where the
runner-appended messages begin so only the new turn is saved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from atom.agent.loop import TurnContext

SKIP_USER_PERSIST_META = "_skip_user_persist"


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether this inbound message should be persisted as user input."""
    return not (metadata and metadata.get(SKIP_USER_PERSIST_META) is True)


def prepare_save_boundary(ctx: TurnContext) -> None:
    """Compute the history append boundary for this turn."""
    ctx.save_skip = _save_skip_for_turn(
        message_metadata=ctx.msg.metadata,
        initial_message_count=len(ctx.initial_messages),
        history_count=len(ctx.history),
        input_persisted_early=ctx.input_persisted_early,
    )


def _save_skip_for_turn(
    *,
    message_metadata: Mapping[str, Any] | None,
    initial_message_count: int,
    history_count: int,
    input_persisted_early: bool,
) -> int:
    """Return the persisted-message append boundary for this turn."""
    if message_metadata and message_metadata.get(SKIP_USER_PERSIST_META) is True:
        return initial_message_count
    # build_messages may merge the current message into a same-role history tail.
    # Runner-appended messages start at initial_message_count in either shape.
    has_standalone_current = initial_message_count > 1 + history_count
    if has_standalone_current and not input_persisted_early:
        return initial_message_count - 1
    return initial_message_count
