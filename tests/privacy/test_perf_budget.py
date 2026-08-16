"""A coarse tripwire on the tokenization engine's cost.

Not a benchmark. ``scripts/bench_privacy.py`` is the benchmark, and it is run by
hand up a ladder of progressively slower hosts because that is what its numbers
mean. This file exists so a change that makes the engine dramatically slower
fails a normal test run instead of waiting for someone to remember the ladder.

Every bound here is deliberately loose — roughly an order of magnitude above what
the dev machine measures — because these run on shared CI hardware where a
tightly-set timing assertion fails for reasons that have nothing to do with the
code. The scaling assertions carry the real signal: a ratio holds across hosts
where an absolute duration does not.
"""

from __future__ import annotations

import time

import pytest

from atom.privacy.stream import PlaceholderStreamResolver
from atom.privacy.tokens import TYPE_EMAIL, TokenStore, detokenize, tokenize


@pytest.fixture
def store(tmp_path) -> TokenStore:
    """A store on the real filesystem: disk cost is part of what is measured."""
    return TokenStore(path=tmp_path / "tokens.json")


def _elapsed_ms(fn, *args, **kwargs) -> float:
    start = time.perf_counter_ns()
    fn(*args, **kwargs)
    return (time.perf_counter_ns() - start) / 1e6


class TestWarmPathIsCheap:
    """Ordinary traffic takes the warm path, so this is the cost that matters.

    An address seen once is a regex match and a dict hit with no disk work. The
    dev machine does ~1.2M addresses/second here; the bound allows 100x worse.
    """

    def test_mapped_addresses_cost_little(self, store: TokenStore) -> None:
        pool = 200
        for i in range(pool):
            store.token_for(TYPE_EMAIL, f"user{i}@example.com")
        text = " ".join(f"contact {i} is user{i}@example.com;" for i in range(pool))
        tokenize(text, store=store)  # discard the first call

        elapsed = _elapsed_ms(tokenize, text, store=store)

        assert elapsed < 20, f"{pool} mapped addresses took {elapsed:.1f}ms"

    def test_warm_path_writes_nothing(self, store: TokenStore) -> None:
        """A repeat must not touch the disk; if it does, the cost is unbounded."""
        store.token_for(TYPE_EMAIL, "alex@example.com")
        before = store.path.stat().st_mtime_ns
        for _ in range(100):
            tokenize("mail alex@example.com", store=store)
        assert store.path.stat().st_mtime_ns == before

    def test_text_with_no_address_is_nearly_free(self, store: TokenStore) -> None:
        """The early exit means most messages never reach the engine proper."""
        text = "the quick brown fox jumps over the lazy dog. " * 200
        tokenize(text, store=store)

        elapsed = _elapsed_ms(tokenize, text, store=store)

        assert elapsed < 5, f"9KB of address-free text took {elapsed:.1f}ms"


class TestEgressIsCheap:
    """Detokenization sits in front of visible output, streamed or whole."""

    def test_resolving_many_placeholders(self, store: TokenStore) -> None:
        tokens = [store.token_for(TYPE_EMAIL, f"user{i}@example.com") for i in range(200)]
        text = " ".join(f"contact {i} is {tokens[i]};" for i in range(200))
        detokenize(text, store=store)

        elapsed = _elapsed_ms(detokenize, text, store=store)

        assert elapsed < 20, f"200 placeholders took {elapsed:.1f}ms"

    def test_streamed_placeholder_per_delta_cost(self, store: TokenStore) -> None:
        """The resolver runs per delta, so its per-call cost is user-visible."""
        token = store.token_for(TYPE_EMAIL, "alex@example.com") or ""
        stream = f"your address is {token} as requested"
        for _ in range(20):  # warm: first calls pay for lazily compiled internals
            warm = PlaceholderStreamResolver()
            for char in stream:
                warm(char, stream_id="warm")
            warm("", stream_id="warm", final=True)

        resolver = PlaceholderStreamResolver()
        worst = 0.0
        for char in stream:
            worst = max(worst, _elapsed_ms(resolver, char, stream_id="s"))
        resolver("", stream_id="s", final=True)

        assert worst < 5, f"slowest delta took {worst:.3f}ms"


class TestPathologicalInputDoesNotStall:
    """Tool output can contain shapes no user would type.

    ``_EMAIL_RE`` carries a lookbehind so a long run of local-part-legal
    characters is tried from one offset rather than every offset. Without it a
    24KB base64 blob followed by a single ``@`` cost ~340ms, and 40KB cost ~1s.
    """

    def test_blob_followed_by_an_at_sign(self, store: TokenStore) -> None:
        text = "QUJDREVG+/=." * 2_000 + "@ "
        elapsed = _elapsed_ms(tokenize, text, store=store)
        assert elapsed < 100, f"24KB blob took {elapsed:.1f}ms"

    def test_growth_is_not_quadratic(self, store: TokenStore) -> None:
        blob = "QUJDREVG+/=."

        def cost(chars: int) -> float:
            return _elapsed_ms(tokenize, blob * (chars // len(blob)) + "@ ", store=store)

        cost(2_000)  # discard
        small = cost(5_000)
        large = cost(20_000)

        # 4x the input: linear is ~4x, quadratic ~16x. The floor on `small` keeps
        # a sub-millisecond baseline from making the ratio meaningless.
        assert large < max(small, 0.05) * 10, f"{small:.3f}ms -> {large:.3f}ms"


class TestColdPathCostIsKnown:
    """Minting is the expensive path, and its shape is a known defect.

    ``TokenStore._save`` reserializes the entire map for every new value, so
    minting *n* addresses is O(n^2) in bytes written — measured at 401x write
    amplification for 800 addresses, ~99% of it in ``json.dumps``. These tests
    pin the shape rather than a duration, so they document the defect and will
    notice if it is fixed or made worse.

    See ``.agent/privacy.md`` and ``scripts/bench_privacy.py``.
    """

    def test_each_new_address_rewrites_the_whole_map(self, store: TokenStore) -> None:
        for i in range(20):
            tokenize(f"user{i}@example.com", store=store)
            assert len(store) == i + 1

        # One save per mint is the defect in one line: were saves batched per
        # call, 20 addresses in 20 calls would still be 20 saves, but the file
        # would not have to be rebuilt from scratch each time.
        assert store.path.stat().st_size > 0

    def test_per_mint_cost_grows_with_map_size(self, store: TokenStore) -> None:
        """Pins the direction, not the magnitude, so it holds on any host."""

        def mint_batch(start: int, count: int = 40) -> float:
            text = " ".join(f"u{i}@example.com" for i in range(start, start + count))
            return _elapsed_ms(tokenize, text, store=store)

        first = mint_batch(0)
        mint_batch(1_000, count=400)  # grow the map
        later = mint_batch(5_000)

        assert later > first, (
            f"minting into a large map ({later:.1f}ms) was not slower than into "
            f"a small one ({first:.1f}ms) — if saves are now batched, update this"
        )

    def test_cap_stops_the_map_growing_without_bound(self, store: TokenStore) -> None:
        """The cap is what keeps the quadratic cost from being unbounded."""
        from atom.privacy.tokens import CAPPED_PLACEHOLDER, MAX_ENTRIES

        assert MAX_ENTRIES == 10_000
        assert CAPPED_PLACEHOLDER == "«email:capped»"
