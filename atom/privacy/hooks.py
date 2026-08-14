"""The ingress boundary where tokenization is applied.

Ingress: user text is tokenized before anything reads it, so session history on
disk, the provider request, and every tool argument carry the placeholder rather
than the address.

Egress is not here: it is a single filter on ``MessageBus.publish_outbound``
(wired in ``AgentLoop.__init__``), so every consumer — channel manager, CLI, SDK
— is covered without each having to remember.

The rule is "who reads it", not "where it goes". The user owns the data and sent
it, so showing it back is not a disclosure, while a placeholder passed to
``web_fetch`` or ``write_file`` stays a placeholder.
"""

from __future__ import annotations

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


#: Without this the model has no way to know a placeholder stands for anything.
#: Observed failure modes it prevents: asking the user for an address they just
#: supplied, "correcting" a placeholder to an invented address, and describing
#: the placeholder to the user instead of just using it.
_TOKEN_GUIDANCE = (
    f"Personal data in this conversation is replaced with placeholders like "
    f"{TOKEN_PATTERN_HINT}. Each one stands for a real value the user provided.",
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
