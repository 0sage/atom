"""Placeholder resolution across stream-delta boundaries.

A model emits ``«email:a91f2c8d»`` as several deltas, so resolving each delta in
isolation finds nothing and the user sees a raw placeholder. This was found by
running the real agent, not by unit tests — streaming is the normal path in both
Telegram and the CLI, so these cases are the common ones.
"""

from __future__ import annotations

import pytest

from atom.privacy import tokens as tokens_module
from atom.privacy.stream import MAX_HELD_CHARS, PlaceholderStreamResolver
from atom.privacy.tokens import TokenStore, tokenize

EMAIL = "alex@example.com"


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


@pytest.fixture
def token(store: TokenStore) -> str:
    return tokenize(EMAIL)


def feed(resolver: PlaceholderStreamResolver, deltas: list[str], sid="s1") -> str:
    """Stream *deltas* through *resolver* and return what the user would see."""
    out = "".join(resolver(d, stream_id=sid) for d in deltas)
    return out + resolver.flush(sid)


class TestSplitAcrossDeltas:
    def test_character_by_character(self, token: str) -> None:
        """The worst case: every character its own delta."""
        assert feed(PlaceholderStreamResolver(), list(token)) == EMAIL

    def test_split_after_opening_guillemet(self, token: str) -> None:
        assert feed(PlaceholderStreamResolver(), ["«", token[1:]]) == EMAIL

    def test_split_mid_identifier(self, token: str) -> None:
        assert feed(PlaceholderStreamResolver(), [token[:10], token[10:]]) == EMAIL

    def test_split_before_closing_guillemet(self, token: str) -> None:
        assert feed(PlaceholderStreamResolver(), [token[:-1], "»"]) == EMAIL

    def test_surrounded_by_text(self, token: str) -> None:
        out = feed(
            PlaceholderStreamResolver(), ["write to ", token[:6], token[6:], " today"]
        )
        assert out == f"write to {EMAIL} today"

    def test_two_placeholders_in_one_stream(self, store: TokenStore) -> None:
        a, b = tokenize("a@example.com"), tokenize("b@example.org")
        out = feed(PlaceholderStreamResolver(), [a[:5], a[5:], " and ", b[:7], b[7:]])
        assert out == "a@example.com and b@example.org"

    def test_whole_placeholder_in_one_delta(self, token: str) -> None:
        assert feed(PlaceholderStreamResolver(), [token]) == EMAIL


class TestNoTextIsLost:
    """A held tail must always be released — dropping it truncates the reply."""

    def test_flush_releases_incomplete_placeholder(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        assert resolver("hello «email:abc", stream_id="s1") == "hello "
        assert resolver.flush("s1") == "«email:abc"

    def test_final_call_releases_held_text(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        resolver("tail «incomplete", stream_id="s1")
        assert resolver("", stream_id="s1", final=True) == "«incomplete"

    def test_plain_text_passes_through_untouched(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        assert feed(resolver, ["hello ", "world"]) == "hello world"

    def test_stray_guillemet_does_not_stall_forever(self, store: TokenStore) -> None:
        """A lone « in prose must be released, not held to the end of the stream."""
        resolver = PlaceholderStreamResolver()
        long_tail = "x" * (MAX_HELD_CHARS + 5)
        out = resolver(f"quote « {long_tail}", stream_id="s1")
        assert out == f"quote « {long_tail}"

    def test_closed_guillemet_pair_is_not_held(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        assert resolver("«unknown» rest", stream_id="s1") == "«unknown» rest"


class TestStreamIsolation:
    def test_streams_do_not_mix_held_text(self, store: TokenStore) -> None:
        a, b = tokenize("a@example.com"), tokenize("b@example.org")
        resolver = PlaceholderStreamResolver()
        resolver(a[:6], stream_id="s1")
        resolver(b[:6], stream_id="s2")
        assert resolver(a[6:], stream_id="s1") == "a@example.com"
        assert resolver(b[6:], stream_id="s2") == "b@example.org"

    def test_discard_drops_state(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        resolver("«email:par", stream_id="s1")
        resolver.discard("s1")
        assert resolver.flush("s1") == ""

    def test_non_streamed_messages_share_a_key(self, token: str) -> None:
        resolver = PlaceholderStreamResolver()
        assert resolver(token, final=True) == EMAIL


class TestUnknownPlaceholders:
    def test_unknown_token_reaches_the_user_intact(self, store: TokenStore) -> None:
        resolver = PlaceholderStreamResolver()
        assert feed(resolver, ["«email:", "deadbeef»"]) == "«email:deadbeef»"
