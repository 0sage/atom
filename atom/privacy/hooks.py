"""The ingress boundaries where tokenization is applied.

Four paths carry outside text into the transcript, and each needs its own hook
because none of them shares a chokepoint with the others:

* user message text — :func:`tokenize_user_text`
* tool output (``exec``, ``web_fetch``, every MCP wrapper) —
  :func:`tokenize_tool_result`
* subagent results, which are injected into history directly
* voice transcription, which becomes text after the message hook has run

Egress is not here: it is a single filter on ``MessageBus.publish_outbound``
(wired in ``AgentLoop.__init__``), so every consumer — channel manager, CLI, SDK
— is covered without each having to remember.

The rule is "who reads it", not "where it goes". The user owns the data and sent
it, so showing it back is not a disclosure, while a placeholder passed to
``web_fetch`` or ``write_file`` stays a placeholder.
"""

from __future__ import annotations

from typing import Any, cast

from atom.agent.tools.context import RequestContext
from atom.privacy.tokens import TOKEN_PATTERN_HINT, tokenize
from atom.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines


def tokenize_user_text(text: str, *, enabled: bool) -> str:
    """Tokenize inbound user text.

    Slash commands are skipped: their arguments are consumed by a command
    handler rather than sent to a model, and ``/secrets set`` in particular must
    reach the store byte-for-byte.
    """
    if not enabled or not text or text.lstrip().startswith("/"):
        return text
    return tokenize(text)


def tokenize_injected_text(text: str, *, enabled: bool) -> str:
    """Tokenize text injected into history without passing the message hook.

    Subagent results and voice transcription both arrive this way. Whole
    addresses are replaced, matching the message path: these are single messages
    rather than bulk data, so keeping the domain readable buys nothing.
    """
    if not enabled or not text:
        return text
    return tokenize(text)


def tokenize_tool_result(result: Any) -> Any:
    """Tokenize contact data anywhere inside a tool result.

    This is the path that carries third-party data in bulk — ``exec`` running a
    query, ``web_fetch`` reading an endpoint, an MCP mail server listing a inbox —
    so it is the larger of the two exposures, not the smaller.

    Results are strings, content-block lists, or dicts depending on the tool, and
    an address can sit at any depth, so the structure is walked. Non-text leaves
    are returned untouched.
    """
    if isinstance(result, str):
        return tokenize(result)
    if isinstance(result, list):
        return [tokenize_tool_result(item) for item in cast(list[Any], result)]
    if isinstance(result, dict):
        return {
            key: tokenize_tool_result(value)
            for key, value in cast(dict[Any, Any], result).items()
        }
    return result


#: Without this the model has no way to know a placeholder stands for anything.
#: Observed failure modes it prevents: asking the user for an address they just
#: supplied, "correcting" a placeholder to an invented address, and describing
#: the placeholder to the user instead of just using it.
_TOKEN_GUIDANCE = (
    f"Personal data in this conversation is replaced with placeholders like "
    f"{TOKEN_PATTERN_HINT}. Each one stands for a real value the user provided.",
    "Data returned by tools carries the same placeholders. Two identical "
    "placeholders are the same person, so you can still group, count and "
    "deduplicate without knowing the values.",
    "Treat a placeholder as an opaque identifier: pass it through unchanged, "
    "never invent or guess the value behind it, and do not ask the user for it "
    "— they already supplied it.",
    "The user sees the real value in your replies, so write placeholders "
    "naturally as if they were the value itself. Do not mention that they are "
    "placeholders.",
)


async def provide_token_runtime_context(
    _request: RequestContext,
) -> RuntimeContextBlock | None:
    """Explain placeholders to the model.

    Registered by ``AgentLoop`` only when tokenization is on, so a session
    without it pays nothing.
    """
    content = wrap_runtime_context_lines(_TOKEN_GUIDANCE)
    if not content:
        return None
    return RuntimeContextBlock(source="privacy_tokens", content=content)
