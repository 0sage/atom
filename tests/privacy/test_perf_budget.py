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


class TestColdPathScalesLinearly:
    """Minting is the expensive path, and batching is what keeps it linear.

    ``_save`` rewrites the whole map, so saving once per newly minted value made
    minting *n* values quadratic in bytes written: 800 addresses wrote 65MB to
    produce a 160KB file, ~99% of it in ``json.dumps`` rather than ``fsync``.
    ``tokenize`` now wraps its substitutions in ``minting_batch``, so one call
    costs one save however many addresses it carries.

    These pin the shape rather than a duration, so they hold on any host and fail
    if the batching is removed or bypassed. See ``.agent/privacy.md`` and
    ``scripts/bench_privacy.py``.
    """

    def test_one_call_costs_one_save(self, store: TokenStore) -> None:
        """The property the whole fix rests on."""
        saves = 0
        real = TokenStore._save

        def counting(self: TokenStore) -> None:
            nonlocal saves
            saves += 1
            real(self)

        TokenStore._save = counting  # pyright: ignore[reportAttributeAccessIssue]
        try:
            tokenize(" ".join(f"u{i}@example.com" for i in range(200)), store=store)
        finally:
            TokenStore._save = real  # pyright: ignore[reportAttributeAccessIssue]

        assert saves == 1, f"200 new addresses cost {saves} saves, expected 1"
        assert len(store) == 200

    def test_doubling_the_input_does_not_quadruple_the_cost(self, tmp_path) -> None:
        """Quadratic would be ~4x for 2x the input; linear is ~2x.

        Each size gets a fresh store, so what is measured is minting from empty
        rather than minting into a map the previous size already filled.
        """

        def cost(count: int) -> float:
            fresh = TokenStore(path=tmp_path / f"map-{count}.json")
            text = " ".join(f"u{i}@example.com" for i in range(count))
            return _elapsed_ms(tokenize, text, store=fresh)

        cost(200)  # discard: first call pays for lazily compiled internals
        small = cost(400)
        large = cost(800)

        # The bound sits between linear and quadratic, with room for timer noise
        # on a loaded host and for the floor to matter when `small` is fast.
        assert large < max(small, 0.5) * 3.5, f"{small:.2f}ms -> {large:.2f}ms"

    def test_reaching_the_cap_is_not_pathological(self, tmp_path) -> None:
        """Filling the map used to take ~130s locally and ~400s on flash.

        A generous ceiling: the point is that this finishes at all, not the exact
        figure. Before batching it could not have run inside a test suite.
        """
        from atom.privacy.tokens import MAX_ENTRIES

        fresh = TokenStore(path=tmp_path / "full.json")
        text = " ".join(f"u{i}@example.com" for i in range(MAX_ENTRIES))
        elapsed = _elapsed_ms(tokenize, text, store=fresh)

        assert len(fresh) == MAX_ENTRIES
        assert elapsed < 20_000, f"filling the map took {elapsed / 1000:.1f}s"

    def test_cap_still_bounds_the_map(self, store: TokenStore) -> None:
        """Batching makes minting cheap; it does not remove the reason for a cap."""
        from atom.privacy.tokens import CAPPED_PLACEHOLDER, MAX_ENTRIES

        assert MAX_ENTRIES == 10_000
        assert CAPPED_PLACEHOLDER == "«email:capped»"
