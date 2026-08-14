"""Resolve placeholders in streamed text, across delta boundaries.

A model emits ``«email:a91f2c8d»`` as several deltas — ``«``, ``email``, ``:``,
and so on — so resolving each delta in isolation finds nothing and the user sees
a raw placeholder. This holds back the tail of a delta when it could be the
start of a placeholder, and releases it once the placeholder completes.

Streaming is on by default in both Telegram and the CLI, so this is the normal
path rather than an edge case.
"""

from __future__ import annotations

from atom.privacy.tokens import detokenize

#: Longest tail held back while waiting for a closing ``»``. A real placeholder
#: is ~17 characters; past this the ``«`` is ordinary text and is released, so a
#: stream containing a stray guillemet cannot stall.
MAX_HELD_CHARS = 48


class PlaceholderStreamResolver:
    """Stateful text filter for outbound content.

    One instance serves every stream, keyed by ``stream_id``, plus non-streamed
    messages under a shared key. Safe to call for content with no placeholders:
    the common path allocates nothing.
    """

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}

    def __call__(
        self,
        text: str,
        *,
        stream_id: str | None = None,
        final: bool = False,
    ) -> str:
        """Return *text* with placeholders resolved, buffering partial ones.

        ``final`` releases any held tail, so the last delta of a stream cannot
        strand text. A non-streamed message passes ``final=True`` and is
        resolved whole.
        """
        key = stream_id or ""
        buffered = self._pending.pop(key, "")
        combined = buffered + text
        if final:
            return detokenize(combined)
        emit, held = _split_at_partial_placeholder(combined)
        if held:
            self._pending[key] = held
        return detokenize(emit)

    def flush(self, stream_id: str | None = None) -> str:
        """Release any held text for a stream without new input."""
        return detokenize(self._pending.pop(stream_id or "", ""))

    def discard(self, stream_id: str | None = None) -> None:
        """Drop held state for an abandoned stream."""
        self._pending.pop(stream_id or "", None)


def _split_at_partial_placeholder(text: str) -> tuple[str, str]:
    """Split *text* into a part safe to emit and a tail that may be incomplete."""
    start = text.rfind("«")
    if start == -1:
        return text, ""
    if "»" in text[start:]:
        # The last opener is already closed, so nothing is pending.
        return text, ""
    if len(text) - start > MAX_HELD_CHARS:
        # Too long to be a placeholder; treat the guillemet as ordinary text
        # rather than holding the stream open indefinitely.
        return text, ""
    return text[:start], text[start:]
