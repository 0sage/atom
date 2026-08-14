"""Minimal command routing table for slash commands."""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from atom.bus.events import OutboundMessage

if TYPE_CHECKING:
    from atom.agent.loop import AgentLoop
    from atom.bus.events import InboundMessage
    from atom.session.manager import Session
    from atom.utils.llm_runtime import LLMRuntime

Handler = Callable[["CommandContext"], Awaitable["OutboundMessage | None"]]
_BOT_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


def normalize_command_text(text: str) -> str:
    """Normalize slash-command transport variants before routing.

    Telegram and Discord-style command dispatch can produce ``/cmd@bot args``.
    The bot suffix belongs to the transport, not the command name, so strip it
    once at the router boundary while preserving user arguments verbatim.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    first, sep, rest = stripped.partition(" ")
    if "@" not in first:
        return stripped
    command, suffix = first.rsplit("@", 1)
    if command and suffix and _BOT_SUFFIX_RE.fullmatch(suffix):
        return f"{command}{sep}{rest}" if sep else command
    return stripped


@dataclass
class CommandContext:
    """Everything a command handler needs to produce a response."""

    msg: InboundMessage
    session: Session | None
    key: str
    raw: str
    args: str = ""
    loop: AgentLoop = field(kw_only=True)
    runtime: LLMRuntime | None = None
    is_user_turn: bool = False
    turn_scopes: list[AbstractContextManager[Any]] = field(default_factory=list)


class CommandRouter:
    """Pure dict-based command dispatch.

    Four tiers checked in order:
      1. *priority* — exact-match commands handled before the dispatch lock
         (e.g. /stop, /restart).
      2. *priority prefix* — argument-taking commands handled before the lock,
         for input that must never be persisted to session history
         (e.g. "/secrets ").
      3. *exact* — exact-match commands handled inside the dispatch lock.
      4. *prefix* — longest-prefix-first match (e.g. "/team ").

    Priority tiers reach handlers through ``dispatch_priority``, which the agent
    loop calls without a session, so neither the command text nor the reply is
    written to history.
    """

    def __init__(self) -> None:
        self._priority: dict[str, Handler] = {}
        self._priority_prefix: list[tuple[str, Handler]] = []
        self._exact: dict[str, Handler] = {}
        self._prefix: list[tuple[str, Handler]] = []

    def priority(self, cmd: str, handler: Handler) -> None:
        self._priority[cmd] = handler

    def priority_prefix(self, pfx: str, handler: Handler) -> None:
        """Register an argument-taking command on the pre-lock tier.

        Use only for commands whose arguments must stay out of session history
        and out of LLM context. Arguments are passed verbatim, without the
        case folding applied to the command name.
        """
        self._priority_prefix.append((pfx, handler))
        self._priority_prefix.sort(key=lambda p: len(p[0]), reverse=True)

    def exact(self, cmd: str, handler: Handler) -> None:
        self._exact[cmd] = handler

    def prefix(self, pfx: str, handler: Handler) -> None:
        self._prefix.append((pfx, handler))
        self._prefix.sort(key=lambda p: len(p[0]), reverse=True)

    def is_priority(self, text: str) -> bool:
        cmd = normalize_command_text(text).lower()
        if cmd in self._priority:
            return True
        return any(cmd.startswith(pfx) for pfx, _ in self._priority_prefix)

    def is_dispatchable_command(self, text: str) -> bool:
        """Check whether *text* should be handled by non-priority dispatch.

        Priority commands are handled separately. Recognized non-priority
        commands and invalid slash commands are dispatched here so malformed
        commands can be rejected instead of reaching the LLM.
        """
        cmd = normalize_command_text(text).lower()
        if self.is_priority(text):
            return False
        if cmd in self._exact:
            return True
        for pfx, _ in self._prefix:
            if cmd.startswith(pfx):
                return True
        return cmd.startswith("/")

    async def dispatch_priority(self, ctx: CommandContext) -> OutboundMessage | None:
        """Dispatch a priority command. Called from run() without the lock."""
        ctx.raw = normalize_command_text(ctx.raw)
        cmd = ctx.raw.lower()
        if handler := self._priority.get(cmd):
            return await handler(ctx)

        for pfx, handler in self._priority_prefix:
            if cmd.startswith(pfx):
                # Slice the original, not the folded copy: arguments may carry a
                # secret value whose case is significant.
                ctx.args = ctx.raw[len(pfx):]
                return await handler(ctx)
        return None

    async def dispatch(self, ctx: CommandContext) -> OutboundMessage | None:
        """Try exact and prefix handlers, then reject invalid slash commands."""
        ctx.raw = normalize_command_text(ctx.raw)
        cmd = ctx.raw.lower()

        if handler := self._exact.get(cmd):
            return await handler(ctx)

        for pfx, handler in self._prefix:
            if cmd.startswith(pfx):
                ctx.args = ctx.raw[len(pfx):]
                return await handler(ctx)

        return self._invalid_command_response(ctx)

    def _invalid_command_response(self, ctx: CommandContext) -> OutboundMessage | None:
        if not ctx.raw.startswith("/"):
            return None

        entered = ctx.raw.split(maxsplit=1)[0]
        commands = self._registered_commands()
        canonical = commands.get(entered.lower())
        if canonical is not None:
            accepts_args = any(
                pfx.rstrip().lower() == entered.lower()
                for pfx, _ in (*self._prefix, *self._priority_prefix)
            )
            if accepts_args:
                content = (
                    f'Invalid command "{entered}". '
                    'Use "/help" to list available commands.'
                )
            else:
                content = (
                    f'Command "{canonical}" does not accept arguments. '
                    f'Did you mean "{canonical}"?'
                )
        else:
            matches = get_close_matches(entered.lower(), commands, n=1, cutoff=0.6)
            if matches:
                content = (
                    f'Unknown command "{entered}". '
                    f'Did you mean "{commands[matches[0]]}"?'
                )
            else:
                content = (
                    f'Unknown command "{entered}". '
                    'Use "/help" to list available commands.'
                )

        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    def _registered_commands(self) -> dict[str, str]:
        commands = [*self._priority, *self._exact]
        commands.extend(
            pfx.rstrip() for pfx, _ in (*self._prefix, *self._priority_prefix)
        )
        return {command.lower(): command for command in commands if command}
