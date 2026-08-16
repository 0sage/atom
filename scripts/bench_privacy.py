"""Measure the tokenization engine's throughput at each boundary it guards.

Runs the privacy engine with no provider in the loop, so what it reports is the
cost atom adds rather than the cost of a turn. Four boundaries are measured
separately because their budgets differ by three orders of magnitude: user text
sits in front of a whole turn, a tool result sits behind a call that already took
100ms, and a stream delta sits directly in front of visible output.

Cold and warm paths are also separated. A first-time address goes through
``token_for`` -> ``_save()``, which serializes the entire map and fsyncs twice;
a repeat hits an in-memory index and writes nothing. One number covering both
hides whichever is broken.

    python3 -m scripts.bench_privacy                     # full run
    python3 -m scripts.bench_privacy --quick             # skip the 10k cases
    python3 -m scripts.bench_privacy --out run.json      # for compare_bench.py

Stdlib only, on purpose. Alpine cannot install the dev extras (``pymupdf`` has no
musl wheel and the failure aborts the whole sync) and a ``uv tool`` install of
atom carries no test dependencies, so a pytest-based benchmark cannot run on two
of the four rungs this is meant to compare. It needs nothing but ``atom.privacy``.

The rungs, slowest storage last, because the local run looks fine and proves
little:

1. the dev machine
2. a lima VM (first honest fsync cost, Linux semantics)
3. an incus container inside that VM (container filesystem layers)
4. the ``atom`` container on nano (flash storage on ARM)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Import before the atom package so a source checkout is preferred over an
# installed copy when both are present.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from atom.privacy import tokens as tokens_mod  # noqa: E402
from atom.privacy.stream import PlaceholderStreamResolver  # noqa: E402
from atom.privacy.tokens import (  # noqa: E402
    MAX_ENTRIES,
    SCHEMA_VERSION,
    TYPE_EMAIL,
    TokenStore,
    detokenize,
    placeholder,
    tokenize,
)

#: Bumped when the shape of the emitted JSON changes, so ``compare_bench.py``
#: can refuse to diff two runs it cannot line up.
RESULT_SCHEMA = 1

#: Provisional. These encode the budgets the boundaries were designed against,
#: not measurements: the ladder is what calibrates them. A gate that fails only
#: on the slowest rung is a finding about the design, not about the number.
#:
#: ``at_most`` for latency, ``at_least`` for throughput — a rate fails by being
#: too low, and gating it with a ceiling would pass a stalled engine.
#:
#: Calibrated against a full ladder run, set to roughly half the slowest rung
#: measured (the Pi container) so the gate catches a regression rather than the
#: hardware. Observed, slowest rung first:
#:
#: ===================  ========  ========  =======
#: case                 Pi (LXC)  lima VM   macOS
#: ===================  ========  ========  =======
#: mint_rate_1s              470       713      814
#: warm_rate_1s          124,830   1.31 M   1.26 M
#: detokenize_rate_1s     43,853   739,680  404,642
#: ===================  ========  ========  =======
#:
#: The mint floor is three orders of magnitude below the warm floor on purpose:
#: minting rewrites the whole map per value, so hundreds per second *is* the
#: design. Gating it where the design actually sits keeps this about regressions
#: rather than restating the defect documented in ``.agent/privacy.md``.
BUDGETS: dict[str, tuple[str, str, float]] = {
    "mint_rate_1s": ("ops_per_second", "at_least", 1_000.0),
    # One call carrying everything, which is what a bulk tool result is. The Pi
    # measures ~85k/s here against ~2.6k/s for the many-call shape; the floor sits
    # well under the slowest rung so it catches a lost batch, not slow hardware.
    "mint_rate_bulk_1s": ("ops_per_second", "at_least", 20_000.0),
    "warm_rate_1s": ("ops_per_second", "at_least", 60_000.0),
    "detokenize_rate_1s": ("ops_per_second", "at_least", 20_000.0),
    "user_text_warm": ("p95_ms", "at_most", 1.0),
    "tool_result_warm_1000": ("total_ms", "at_most", 200.0),
    "detokenize_1000": ("total_ms", "at_most", 200.0),
    # Slowest rung measured p99 0.021ms and max 0.038ms; 0.5 leaves room for a
    # loaded host without admitting a boundary that blocks visible output.
    "stream_char_by_char": ("p99_ms", "at_most", 0.5),
    "no_match_baseline": ("p99_ms", "at_most", 0.1),
    # The ReDoS guard. Unguarded, these shapes cost 340ms-1s; the slowest rung
    # measures 3.9ms with the lookbehind in place.
    "regex_pathological": ("max_ms", "at_most", 100.0),
}


# -- measurement -------------------------------------------------------------


@dataclass
class IOCounters:
    """Disk work attributable to a case.

    ``fsyncs`` is the number that matters. ``_save`` fsyncs the file and then
    its parent directory, so a case that mints *n* values performs 2n of them
    while rewriting a map that grows with every one — the quadratic byte count
    is what makes an O(n^2) write pattern visible instead of merely slow.
    """

    saves: int = 0
    fsyncs: int = 0
    bytes_serialized: int = 0
    proc_write_bytes: int | None = None


@dataclass
class Sample:
    """One timed run of a case."""

    total_ms: float
    ops: int
    latencies_ms: list[float] = field(default_factory=list[float])
    io: IOCounters = field(default_factory=IOCounters)
    notes: dict[str, Any] = field(default_factory=dict[str, Any])


#: A case slower than this is recorded as ``exceeded`` and its remaining repeats
#: are skipped. Cold minting is quadratic in the number of new values, so the 10k
#: case takes ~130s on fast local storage and correspondingly longer on flash —
#: enough to make the ladder unrunnable. Refusing to wait turns that into a
#: recorded measurement instead of a hang, which is the finding either way.
DEFAULT_DEADLINE_SECONDS = 120.0


#: Untimed iterations before a per-call case starts recording. The first call
#: into the engine pays for lazily compiled regex internals and cold caches, and
#: with a few hundred samples the nearest-rank p99 *is* the maximum — so without
#: this the tail reports startup cost rather than steady state, and a budget set
#: against it would be measuring the wrong thing.
WARMUP_ITERATIONS = 50


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Interpolation is deliberately avoided: the tail here is fsync stalls, and
    averaging a stall with its neighbour reports a latency that never happened.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(fraction * len(ordered) + 0.9999) - 1))
    return ordered[index]


def _proc_write_bytes() -> int | None:
    """Bytes this process has sent to storage, where the kernel exposes it.

    Linux only. Absent on macOS, which is why the serialized byte count is
    tracked separately rather than relying on this.
    """
    try:
        text = Path("/proc/self/io").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("write_bytes:"):
            return int(line.split(":", 1)[1])
    return None


@contextmanager
def _counting_io() -> Iterator[IOCounters]:
    """Count map writes and fsyncs without changing what reaches the disk.

    Both wrappers delegate to the real implementation: the storage layer is the
    variable this benchmark exists to compare across rungs, so faking a write
    would measure nothing worth knowing.
    """
    counters = IOCounters()
    real_write = tokens_mod.write_private_text
    real_fsync = os.fsync
    before = _proc_write_bytes()

    def counting_write(path: Path, content: str) -> None:
        counters.saves += 1
        counters.bytes_serialized += len(content.encode("utf-8"))
        real_write(path, content)

    def counting_fsync(fd: Any) -> None:
        counters.fsyncs += 1
        real_fsync(fd)

    tokens_mod.write_private_text = counting_write  # pyright: ignore[reportAttributeAccessIssue]
    os.fsync = counting_fsync  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield counters
    finally:
        tokens_mod.write_private_text = real_write  # pyright: ignore[reportAttributeAccessIssue]
        os.fsync = real_fsync  # pyright: ignore[reportAttributeAccessIssue]
        after = _proc_write_bytes()
        if before is not None and after is not None:
            counters.proc_write_bytes = after - before


# -- corpora -----------------------------------------------------------------


def address(index: int) -> str:
    """A deterministic address for *index*.

    No RNG at all, not even a seeded one: every rung must tokenize byte-identical
    input or the comparison between them measures the corpus as well as the host.
    """
    return f"user.{index:06d}@d{index % 250}.example.com"


def unique_corpus(count: int) -> str:
    """Prose carrying *count* distinct addresses."""
    return " ".join(f"contact {i} is {address(i)};" for i in range(count))


def repeated_corpus(count: int) -> str:
    """Prose carrying one address *count* times."""
    single = address(0)
    return " ".join(f"contact {i} is {single};" for i in range(count))


def seeded_store_and_corpus(path: Path, count: int) -> tuple[TokenStore, str]:
    """A store holding *count* entries, plus prose carrying all their placeholders.

    The map is written in one go and the store built afterwards, rather than
    minting value by value: minting is quadratic — ~130s for 10k on fast local
    storage — and that cost would land in a case's *setup*, outside the timed
    region, where it looks like a hang rather than a measurement. Egress is what
    this corpus is for and it cannot tell how the entries got there.

    Building the store after the file exists is enough to pick it up, because
    ``_load`` is lazy and runs on first use.
    """
    seeded_map(path, count)
    store = TokenStore(path=path)
    entries: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["entries"]
    tokens = list(entries)
    text = " ".join(f"contact {i} is {tokens[i]};" for i in range(count))
    return store, text


def seeded_map(path: Path, count: int) -> None:
    """Write a map holding *count* entries directly.

    Minting them through ``token_for`` would cost O(count^2) bytes to set up a
    case about a single mint. The file is the same shape ``_save`` produces, so
    what is being measured is unaffected.
    """
    stamp = "2026-01-01T00:00:00Z"
    entries = {
        placeholder(TYPE_EMAIL, f"{i:08x}"): {
            "type": TYPE_EMAIL,
            "value": address(i),
            "created": stamp,
            "last_used": stamp,
            "hits": 0,
        }
        for i in range(count)
    }
    payload = {"version": SCHEMA_VERSION, "entries": entries}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# -- cases -------------------------------------------------------------------

#: Every case gets one of these, holding a directory on the filesystem under
#: test. Nothing is monkeypatched away from the real disk.
CaseFn = Callable[[Path], Sample]


def _timed_bulk(text: str, store: TokenStore, fn: Callable[[str, TokenStore], str],
                ops: int) -> Sample:
    """One call over a whole corpus: total time and derived rate."""
    with _counting_io() as io:
        start = time.perf_counter_ns()
        fn(text, store)
        elapsed = time.perf_counter_ns() - start
    return Sample(total_ms=elapsed / 1e6, ops=ops, io=io)


#: Addresses per call on the drip-feed case. Represents a stream of small tool
#: results rather than one bulk one, which is the shape that pays a save per call.
_DRIP_SLICE = 25

#: Addresses minted by the pre-flight probe. Small enough to be cheap on flash,
#: large enough that a per-value save is unmistakable.
_PROBE_SIZE = 100


def _saves_per_call(workdir: Path) -> int:
    """Count how many times one ``tokenize`` call writes the map.

    The guard on the large cold cases. One save per call is the batched design and
    linear; one save per *value* is the old quadratic behaviour, which takes ~130s
    locally and ~400s on flash to reach ``MAX_ENTRIES`` and would make the ladder
    unrunnable.

    Counting saves rather than timing two probe sizes and inferring an exponent:
    the exponent approach was tried and does not work at probe scale, because the
    fixed per-save cost still dominates at a few hundred values and a regressed
    engine reads as merely slow. This tests the property itself, so it cannot
    confuse a slow host with a regression — which is the whole job.
    """
    store = TokenStore(path=workdir / "probe.json")
    text = " ".join(
        f"p{i} is probe.{i:06d}@probe.example.com;" for i in range(_PROBE_SIZE)
    )
    saves = 0
    real = TokenStore._save

    def counting(self: TokenStore) -> None:
        nonlocal saves
        saves += 1
        real(self)

    TokenStore._save = counting  # pyright: ignore[reportAttributeAccessIssue]
    try:
        tokenize(text, store)
    finally:
        TokenStore._save = real  # pyright: ignore[reportAttributeAccessIssue]
    return saves


def case_tool_result_cold(count: int, deadline_s: float) -> CaseFn:
    """Bulk tool output where every address is new — the minting path.

    This is the case ``MAX_ENTRIES`` was written for: a single ``grep -r "@"``
    over a mail directory carries thousands of addresses the agent merely passed
    over. At ``count=10000`` it fills the map exactly.

    The corpus is passed in **one** ``tokenize`` call, because that is what a tool
    result is. This matters more than it sounds: ``tokenize`` saves once per call,
    so feeding the same corpus in slices measures the harness's own call boundaries
    instead of the engine — 10k addresses cost 60ms in one call and 5.1s in 400
    slices on the same host. See :func:`case_mint_drip` for the sliced shape,
    measured deliberately rather than by accident.
    """

    def run(workdir: Path) -> Sample:
        # Only the large cases are worth guarding; a small one finishes either
        # way, and running it unguarded keeps a regression visible as a slow
        # number rather than a skip.
        if count > _PROBE_SIZE:
            saves = _saves_per_call(workdir)
            if saves > 1:
                return Sample(
                    total_ms=0.0,
                    ops=0,
                    notes={
                        "requested": count,
                        "skipped": (
                            f"minting is not batched: {saves} saves for "
                            f"{_PROBE_SIZE} values, so this case is quadratic"
                        ),
                        "saves_per_call": saves,
                        "aborted_at_deadline_s": deadline_s,
                    },
                )
        store = TokenStore(path=workdir / "tokens.json")
        text = " ".join(f"contact {i} is {address(i)};" for i in range(count))
        with _counting_io() as io:
            start = time.perf_counter_ns()
            tokenize(text, store)
            elapsed = time.perf_counter_ns() - start
        sample = Sample(total_ms=elapsed / 1e6, ops=count, io=io)
        sample.notes["map_entries"] = len(store)
        sample.notes["requested"] = count
        sample.notes["map_bytes"] = store.path.stat().st_size if store.path.exists() else 0
        return sample

    return run


def case_mint_drip(count: int, deadline_s: float) -> CaseFn:
    """The same addresses arriving as many small calls instead of one.

    A stream of small tool results, or a chatty MCP server. Each call saves once,
    so this is where per-call durability cost shows up, and it is the upper bound
    on what batching can do: batching collapses saves *within* a call and cannot
    collapse saves across calls. Worth measuring because the gap between this and
    :func:`case_tool_result_cold` is the argument for any future cross-call
    coalescing.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        segments = [f"contact {i} is {address(i)};" for i in range(count)]
        done = 0
        budget_ns = int(deadline_s * 1e9)
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for offset in range(0, count, _DRIP_SLICE):
                tokenize(" ".join(segments[offset:offset + _DRIP_SLICE]), store)
                done += min(_DRIP_SLICE, count - offset)
                if time.perf_counter_ns() - start > budget_ns:
                    break
            elapsed = time.perf_counter_ns() - start
        sample = Sample(total_ms=elapsed / 1e6, ops=done, io=io)
        sample.notes["map_entries"] = len(store)
        sample.notes["requested"] = count
        sample.notes["calls"] = (done + _DRIP_SLICE - 1) // _DRIP_SLICE
        if done < count:
            sample.notes["aborted_at_deadline_s"] = deadline_s
        return sample

    return run


def case_mint_rate(budget_s: float = 1.0) -> CaseFn:
    """How many previously unseen addresses can be tokenized in *budget_s*.

    "How many emails a second" for the expensive path, and the pessimistic answer
    of the two: filling a one-second budget requires many calls, each of which
    saves once, so this measures per-call durability against a map that grows
    throughout. The optimistic answer — one call carrying everything — is
    :func:`case_tool_result_cold`, and on the same host the two differ by ~40x.

    Both are real. This one is a chatty stream of small results; that one is a
    single bulk result. Neither alone describes the engine.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        budget_ns = int(budget_s * 1e9)
        minted = 0
        with _counting_io() as io:
            start = time.perf_counter_ns()
            while time.perf_counter_ns() - start < budget_ns:
                chunk = " ".join(
                    f"contact {i} is {address(i)};"
                    for i in range(minted, minted + _DRIP_SLICE)
                )
                tokenize(chunk, store)
                minted += _DRIP_SLICE
            elapsed = time.perf_counter_ns() - start
        return Sample(
            total_ms=elapsed / 1e6,
            ops=minted,
            io=io,
            notes={
                "budget_s": budget_s,
                "per_call": _DRIP_SLICE,
                "map_entries": len(store),
                "map_bytes": store.path.stat().st_size if store.path.exists() else 0,
            },
        )

    return run


def case_mint_rate_bulk(budget_s: float = 1.0) -> CaseFn:
    """New addresses per second when they arrive in one call, not many.

    The shape a bulk tool result actually has, and what batching was built for:
    one save covers every address in the call. Sized from a probe so the call is
    large enough to fill the budget without overshooting it badly.
    """

    def run(workdir: Path) -> Sample:
        probe = TokenStore(path=workdir / "probe.json")
        probe_text = " ".join(
            f"p{i} is probe.{i:06d}@probe.example.com;" for i in range(_PROBE_SIZE)
        )
        start = time.perf_counter_ns()
        tokenize(probe_text, probe)
        per_value_ms = (time.perf_counter_ns() - start) / 1e6 / _PROBE_SIZE
        # Cap at MAX_ENTRIES: past it `token_for` returns the capped placeholder
        # and stops storing, which would measure the cap rather than minting.
        count = max(_PROBE_SIZE, min(MAX_ENTRIES, int(budget_s * 1e3 / max(per_value_ms, 1e-6))))

        store = TokenStore(path=workdir / "tokens.json")
        text = " ".join(f"contact {i} is {address(i)};" for i in range(count))
        with _counting_io() as io:
            start = time.perf_counter_ns()
            tokenize(text, store)
            elapsed = time.perf_counter_ns() - start
        return Sample(
            total_ms=elapsed / 1e6,
            ops=count,
            io=io,
            notes={
                "budget_s": budget_s,
                "single_call_addresses": count,
                "map_entries": len(store),
            },
        )

    return run


def case_warm_rate(budget_s: float = 1.0) -> CaseFn:
    """How many already-mapped addresses can be tokenized in *budget_s*.

    The same question on the cheap path, which is what ordinary traffic takes:
    an address seen once is a regex match and a dict hit with no disk work. The
    gap between this and :func:`case_mint_rate` is the cost of minting.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        pool = 200
        for i in range(pool):
            store.token_for(TYPE_EMAIL, address(i))
        text = " ".join(f"contact {i} is {address(i)};" for i in range(pool))
        budget_ns = int(budget_s * 1e9)
        seen = 0
        with _counting_io() as io:
            start = time.perf_counter_ns()
            while time.perf_counter_ns() - start < budget_ns:
                tokenize(text, store)
                seen += pool
            elapsed = time.perf_counter_ns() - start
        return Sample(
            total_ms=elapsed / 1e6,
            ops=seen,
            io=io,
            notes={"budget_s": budget_s, "pool": pool},
        )

    return run


def case_detokenize_rate(budget_s: float = 1.0) -> CaseFn:
    """How many placeholders can be resolved in *budget_s* — the egress path."""

    def run(workdir: Path) -> Sample:
        pool = 200
        store, text = seeded_store_and_corpus(workdir / "tokens.json", pool)
        budget_ns = int(budget_s * 1e9)
        resolved = 0
        with _counting_io() as io:
            start = time.perf_counter_ns()
            while time.perf_counter_ns() - start < budget_ns:
                detokenize(text, store)
                resolved += pool
            elapsed = time.perf_counter_ns() - start
        return Sample(
            total_ms=elapsed / 1e6,
            ops=resolved,
            io=io,
            notes={"budget_s": budget_s, "pool": pool},
        )

    return run


def case_tool_result_warm(count: int) -> CaseFn:
    """Bulk tool output where the address is already mapped — regex plus a dict.

    Nothing should reach the disk here, and the ``saves`` counter says whether
    that holds.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        text = repeated_corpus(count)
        store.token_for(TYPE_EMAIL, address(0))
        return _timed_bulk(text, store, tokenize, count)

    return run


def case_user_text_warm() -> CaseFn:
    """A message with two addresses, both already mapped.

    Sized to what a user actually sends. This one sits in front of the whole
    turn, so it is measured per call rather than in bulk.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        text = f"forward it to {address(0)} and cc {address(1)} please"
        store.token_for(TYPE_EMAIL, address(0))
        store.token_for(TYPE_EMAIL, address(1))
        for _ in range(WARMUP_ITERATIONS):
            tokenize(text, store)
        latencies: list[float] = []
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for _ in range(1000):
                op = time.perf_counter_ns()
                tokenize(text, store)
                latencies.append((time.perf_counter_ns() - op) / 1e6)
            total = time.perf_counter_ns() - start
        return Sample(total_ms=total / 1e6, ops=1000, latencies_ms=latencies, io=io)

    return run


def case_mint_at_map_size(size: int) -> CaseFn:
    """Per-mint latency against a map already holding *size* entries.

    The prediction under test: ``_save`` rewrites the whole map, so a mint costs
    more the fuller the map is. If the tail here grows with *size*, cold-path
    minting needs batching rather than one save per value.
    """

    def run(workdir: Path) -> Sample:
        path = workdir / "tokens.json"
        if size:
            seeded_map(path, size)
        store = TokenStore(path=path)
        latencies: list[float] = []
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for i in range(100):
                value = f"fresh.{i:04d}@mint.example.com"
                op = time.perf_counter_ns()
                store.token_for(TYPE_EMAIL, value)
                latencies.append((time.perf_counter_ns() - op) / 1e6)
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=100,
            latencies_ms=latencies,
            io=io,
            notes={"seeded_entries": size, "map_entries": len(store)},
        )

    return run


def case_cap_boundary() -> CaseFn:
    """Minting past ``MAX_ENTRIES``, where values are replaced but not stored.

    The capped path returns an unresolvable marker instead of the plaintext, so
    the size limit cannot turn into a disclosure. It must also not be slower than
    minting, or a full map becomes a way to slow the agent down.
    """

    def run(workdir: Path) -> Sample:
        path = workdir / "tokens.json"
        seeded_map(path, MAX_ENTRIES)
        store = TokenStore(path=path)
        text = unique_corpus(1000)
        sample = _timed_bulk(text, store, tokenize, 1000)
        sample.notes["map_entries"] = len(store)
        sample.notes["capped"] = tokens_mod.CAPPED_PLACEHOLDER in tokenize(text, store)
        return sample

    return run


def case_detokenize(count: int) -> CaseFn:
    """Egress: resolving *count* placeholders in one reply.

    Resolution also bumps usage counters. The first one writes through and the
    rest are throttled, so ``saves`` here should be 1 however large *count* is.
    """

    def run(workdir: Path) -> Sample:
        store, text = seeded_store_and_corpus(workdir / "tokens.json", count)
        return _timed_bulk(text, store, detokenize, count)

    return run


def case_stream_char_by_char() -> CaseFn:
    """A placeholder arriving one character per delta — the resolver's worst case.

    Each delta is a separate call that blocks text reaching the user, so this is
    measured per call. Every character of ``«email:a91f2c8d»`` is held back until
    the closing guillemet arrives.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        token = store.token_for(TYPE_EMAIL, address(0)) or ""
        stream = f"your address is {token} as requested, and again {token} for clarity"
        for _ in range(WARMUP_ITERATIONS):
            warm = PlaceholderStreamResolver()
            for char in stream:
                warm(char, stream_id="warmup")
            warm("", stream_id="warmup", final=True)
        resolver = PlaceholderStreamResolver()
        latencies: list[float] = []
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for char in stream:
                op = time.perf_counter_ns()
                resolver(char, stream_id="bench")
                latencies.append((time.perf_counter_ns() - op) / 1e6)
            resolver("", stream_id="bench", final=True)
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=len(stream),
            latencies_ms=latencies,
            io=io,
            notes={"stream_chars": len(stream)},
        )

    return run


def case_stream_unclosed() -> CaseFn:
    """Guillemets that never close, one every ``MAX_HELD_CHARS``.

    Exercises the release path that stops a stray ``«`` from stalling a stream
    indefinitely. A regression here shows up as held text, not as latency, so the
    output length is recorded alongside the timing.
    """

    def run(_workdir: Path) -> Sample:
        resolver = PlaceholderStreamResolver()
        chunk = "«" + "x" * 60
        deltas = [chunk] * 200
        for _ in range(WARMUP_ITERATIONS):
            warm = PlaceholderStreamResolver()
            warm(chunk, stream_id="warmup")
            warm("", stream_id="warmup", final=True)
        latencies: list[float] = []
        emitted = 0
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for delta in deltas:
                op = time.perf_counter_ns()
                emitted += len(resolver(delta, stream_id="unclosed"))
                latencies.append((time.perf_counter_ns() - op) / 1e6)
            emitted += len(resolver("", stream_id="unclosed", final=True))
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=len(deltas),
            latencies_ms=latencies,
            io=io,
            notes={"input_chars": len(chunk) * len(deltas), "emitted_chars": emitted},
        )

    return run


def case_no_match_baseline() -> CaseFn:
    """Text with no ``@`` and no ``«`` — the case almost every call is.

    Both functions return the input untouched before doing any work. If this is
    measurably above the flag-off baseline, those early exits are not working and
    every ordinary message is paying for a feature it does not use.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        text = "the quick brown fox jumps over the lazy dog. " * 200
        for _ in range(WARMUP_ITERATIONS):
            tokenize(text, store)
            detokenize(text, store)
        latencies: list[float] = []
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for _ in range(2000):
                op = time.perf_counter_ns()
                tokenize(text, store)
                detokenize(text, store)
                latencies.append((time.perf_counter_ns() - op) / 1e6)
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=2000,
            latencies_ms=latencies,
            io=io,
            notes={"text_chars": len(text)},
        )

    return run


def case_regex_pathological() -> CaseFn:
    """Shapes built to make ``_EMAIL_RE`` work hard without matching.

    Many bare ``@`` defeat the cheap ``"@" not in text`` exit, and a very long
    local part is where a careless pattern backtracks. Growth that is not linear
    in input length is the failure.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        corpora = {
            "bare_at": "@ " * 5000,
            "at_no_domain": "user@ " * 2000,
            "trailing_dots": "user@example. " * 2000,
            "long_local": ("a" * 10_000) + "@ ",
            "long_local_matching": ("a" * 10_000) + "@example.com ",
            "hyphen_runs": ("user@" + "a-" * 500 + "b.com ") * 20,
        }
        latencies: list[float] = []
        per_corpus: dict[str, float] = {}
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for name, text in corpora.items():
                op = time.perf_counter_ns()
                tokenize(text, store)
                elapsed = (time.perf_counter_ns() - op) / 1e6
                latencies.append(elapsed)
                per_corpus[name] = round(elapsed, 4)
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=len(corpora),
            latencies_ms=latencies,
            io=io,
            notes={"per_corpus_ms": per_corpus},
        )

    return run


def case_concurrent(threads: int) -> CaseFn:
    """*threads* tokenizing against one store at once.

    ``TokenStore`` serializes on an ``RLock`` held across the save, so concurrent
    minting is expected to queue. What this answers is whether it queues or
    degrades: compare the rate against the single-threaded cold case.
    """

    def run(workdir: Path) -> Sample:
        store = TokenStore(path=workdir / "tokens.json")
        per_thread = 200
        barrier = threading.Barrier(threads)
        durations: list[float] = []
        lock = threading.Lock()

        def worker(slot: int) -> None:
            text = " ".join(
                address(slot * per_thread + i) for i in range(per_thread)
            )
            barrier.wait()
            op = time.perf_counter_ns()
            tokenize(text, store)
            elapsed = (time.perf_counter_ns() - op) / 1e6
            with lock:
                durations.append(elapsed)

        workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
        with _counting_io() as io:
            start = time.perf_counter_ns()
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join()
            total = time.perf_counter_ns() - start
        return Sample(
            total_ms=total / 1e6,
            ops=threads * per_thread,
            latencies_ms=durations,
            io=io,
            notes={"threads": threads, "per_thread": per_thread, "map_entries": len(store)},
        )

    return run


def case_flag_off(count: int) -> CaseFn:
    """The same bulk corpus with tokenization off.

    The baseline every other number is reported against. "Tokenization costs X"
    only means something next to the identical corpus not being tokenized, and
    the hooks skip the engine entirely when the flag is false.
    """

    def run(_workdir: Path) -> Sample:
        text = unique_corpus(count)
        with _counting_io() as io:
            start = time.perf_counter_ns()
            # What the hooks do when the flag is off: hand the text straight back.
            result = text
            elapsed = time.perf_counter_ns() - start
        return Sample(
            total_ms=elapsed / 1e6,
            ops=count,
            io=io,
            notes={"chars": len(result)},
        )

    return run


def build_cases(quick: bool, deadline_s: float) -> dict[str, CaseFn]:
    """Assemble the case table.

    ``quick`` drops the cases whose cost grows with a large map. Those are the
    ones that matter on slow storage, so it is for iterating on the harness
    rather than for a rung of the ladder.

    The ``*_rate`` cases come first because they answer the original question —
    how many addresses a second — and they are bounded by construction, so they
    produce a comparable number on every rung.

    Minting appears twice on purpose. ``mint_rate_1s`` fills its budget with many
    small calls and ``mint_rate_bulk_1s`` uses one large call; since ``tokenize``
    saves once per call, the two differ by ~40x on the same host. Reporting only
    one would misstate the engine in whichever direction that one was chosen.
    """
    big = [] if quick else [10_000]
    cases: dict[str, CaseFn] = {}

    cases["mint_rate_1s"] = case_mint_rate(1.0)
    cases["mint_rate_bulk_1s"] = case_mint_rate_bulk(1.0)
    cases["warm_rate_1s"] = case_warm_rate(1.0)
    cases["detokenize_rate_1s"] = case_detokenize_rate(1.0)

    for n in [1, 10, 100, 1_000, *big]:
        cases[f"tool_result_cold_{n}"] = case_tool_result_cold(n, deadline_s)
    for n in [1_000, *big]:
        cases[f"mint_drip_{n}"] = case_mint_drip(n, deadline_s)
    for n in [100, 1_000, *big]:
        cases[f"tool_result_warm_{n}"] = case_tool_result_warm(n)
    cases["user_text_warm"] = case_user_text_warm()
    for size in [0, 1_000, *big]:
        cases[f"mint_at_map_size_{size}"] = case_mint_at_map_size(size)
    for n in [100, 1_000, *big]:
        cases[f"detokenize_{n}"] = case_detokenize(n)
    cases["stream_char_by_char"] = case_stream_char_by_char()
    cases["stream_unclosed"] = case_stream_unclosed()
    cases["no_match_baseline"] = case_no_match_baseline()
    cases["regex_pathological"] = case_regex_pathological()
    for threads in [2, 8]:
        cases[f"concurrent_{threads}"] = case_concurrent(threads)
    for n in [1_000, *big]:
        cases[f"flag_off_{n}"] = case_flag_off(n)
    if not quick:
        cases["cap_boundary"] = case_cap_boundary()
    return cases


# -- environment -------------------------------------------------------------


def fsync_cost_ms(workdir: Path, iterations: int = 50) -> dict[str, float]:
    """Time a bare fsync on the filesystem under test.

    Reported next to the results so a slow rung is attributable. Without it, a
    case that takes 40x longer on the Pi is a mystery rather than a measurement
    of its storage.
    """
    path = workdir / "fsync_probe"
    latencies: list[float] = []
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        for i in range(iterations):
            os.write(fd, f"{i:07d}\n".encode())
            start = time.perf_counter_ns()
            os.fsync(fd)
            latencies.append((time.perf_counter_ns() - start) / 1e6)
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)
    return {
        "p50_ms": round(_percentile(latencies, 0.50), 4),
        "p95_ms": round(_percentile(latencies, 0.95), 4),
        "max_ms": round(max(latencies), 4),
    }


def describe_environment(workdir: Path) -> dict[str, Any]:
    """Everything needed to attribute a number to a host."""
    info: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "atom_version": _atom_version(),
        "workdir": str(workdir),
        "fsync": fsync_cost_ms(workdir),
    }
    try:
        load = os.getloadavg()
        info["loadavg"] = [round(value, 2) for value in load]
    except (OSError, AttributeError):
        info["loadavg"] = None
    try:
        stat = os.statvfs(workdir)
        info["filesystem"] = {
            "block_size": stat.f_bsize,
            "free_bytes": stat.f_bavail * stat.f_frsize,
        }
    except (OSError, AttributeError):
        info["filesystem"] = None
    info["container"] = _detect_container()
    return info


def _atom_version() -> str:
    try:
        import atom

        return str(getattr(atom, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 - a version string is not worth failing over
        return "unknown"


def _detect_container() -> str | None:
    """Best-effort note on whether this rung is containerized.

    Advisory only, and recorded rather than acted on: the reason a rung is slow
    should be visible in the output, and "which rung was this" is easy to lose
    once four JSON files exist.

    Device and marker files are checked before ``/proc/1/cgroup`` because the
    cgroup is not reliable here. Inside an incus/LXC container running systemd,
    PID 1 reads ``0::/init.scope`` with no ``lxc`` anywhere in it, so cgroup
    sniffing alone reports "host" on exactly the two rungs whose containerization
    matters most. ``/dev/incus`` is present in an incus guest and absent on the
    host, and ``container=`` in PID 1's environment is what LXC itself sets.
    """
    for marker, label in (
        ("/dev/incus", "incus"),
        ("/dev/lxd", "lxd"),
        ("/run/.containerenv", "podman"),
        ("/.dockerenv", "docker"),
    ):
        if Path(marker).exists():
            return label
    try:
        # NUL-separated, and the value is what the runtime named itself: `lxc`
        # for LXC/incus, `podman`, `systemd-nspawn`, and so on.
        for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
            if item.startswith(b"container="):
                return item.split(b"=", 1)[1].decode("utf-8", "replace") or None
    except OSError:
        # Unreadable when not privileged, so a missing answer here is normal
        # rather than an error worth surfacing.
        pass
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for needle in ("lxc", "docker", "kubepods"):
        if needle in cgroup:
            return needle
    return None


# -- driver ------------------------------------------------------------------


def summarize(samples: list[Sample]) -> dict[str, Any]:
    """Collapse repeats into one row, keeping the spread visible.

    A single run on flash storage can catch a cache flush or a thermal dip, and
    that is indistinguishable from a regression unless the spread is reported.
    """
    totals = [s.total_ms for s in samples]
    # Median rather than the first sample's: a rate case does a different amount
    # of work each repeat, and a deadline case may stop at a different point.
    ops = int(statistics.median([s.ops for s in samples]))
    latencies = [value for s in samples for value in s.latencies_ms]
    best = min(samples, key=lambda s: s.total_ms)
    median_ms = statistics.median(totals)
    summary: dict[str, Any] = {
        "ops": ops,
        "repeats": len(samples),
        "total_ms_min": round(min(totals), 4),
        "total_ms_median": round(median_ms, 4),
        "total_ms_max": round(max(totals), 4),
        "total_ms": round(median_ms, 4),
        # Suppressed below ~timer resolution: dividing an op count by a duration
        # that rounds to zero reports a rate in the billions, which reads as a
        # result rather than as "too fast to time this way".
        "ops_per_second": round(ops / (median_ms / 1000), 1) if median_ms >= 0.01 else None,
        "saves": best.io.saves,
        "fsyncs": best.io.fsyncs,
        "bytes_serialized": best.io.bytes_serialized,
        "proc_write_bytes": best.io.proc_write_bytes,
        "notes": best.notes,
    }
    if latencies:
        summary.update(
            {
                "p50_ms": round(_percentile(latencies, 0.50), 4),
                "p95_ms": round(_percentile(latencies, 0.95), 4),
                "p99_ms": round(_percentile(latencies, 0.99), 4),
                "max_ms": round(max(latencies), 4),
            }
        )
    return summary


def check_budgets(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare the summaries against :data:`BUDGETS`."""
    gates: list[dict[str, Any]] = []
    for case, (metric, direction, limit) in BUDGETS.items():
        base = {"case": case, "metric": metric, "direction": direction, "limit": limit}
        summary = results.get(case)
        if summary is None:
            gates.append({**base, "status": "skipped"})
            continue
        value = summary.get(metric)
        if not isinstance(value, (int, float)):
            gates.append({**base, "status": "missing"})
            continue
        number = float(value)
        ok = number >= limit if direction == "at_least" else number <= limit
        gates.append(
            {**base, "value": round(number, 4), "status": "pass" if ok else "FAIL"}
        )
    return gates


def run(selected: dict[str, CaseFn], repeats: int, root: Path) -> dict[str, dict[str, Any]]:
    """Run every case *repeats* times, each in its own directory.

    A case that hit its deadline is not repeated: the repeats exist to show the
    spread of a fast measurement, and paying the deadline twice more to learn the
    same thing would cost minutes per case on the slowest rung.
    """
    results: dict[str, dict[str, Any]] = {}
    for name, case in selected.items():
        samples: list[Sample] = []
        for attempt in range(repeats):
            workdir = root / f"{name}-{attempt}"
            workdir.mkdir(parents=True, exist_ok=True)
            samples.append(case(workdir))
            if "aborted_at_deadline_s" in samples[-1].notes:
                break
        results[name] = summarize(samples)
        line = results[name]
        detail = f"{line['total_ms']:>10.1f} ms  {line['ops']:>7} ops"
        if line.get("ops_per_second"):
            detail += f"  {line['ops_per_second']:>11,.0f} ops/s"
        if line["fsyncs"]:
            detail += f"  {line['fsyncs']:>6} fsync"
        if "aborted_at_deadline_s" in line["notes"]:
            requested = line["notes"].get("requested")
            detail += f"  ABORTED at {line['ops']}/{requested}"
        print(f"  {name:<28} {detail}", file=sys.stderr)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--quick", action="store_true", help="skip the 10k cases")
    parser.add_argument("--out", type=Path, help="write JSON here as well as stdout")
    parser.add_argument("--label", default="", help="name this rung in the output")
    parser.add_argument(
        "--workdir",
        type=Path,
        help="run against this directory instead of the system temp dir, "
        "for measuring a specific filesystem",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="run only cases whose name contains this (repeatable)",
    )
    parser.add_argument(
        "--deadline",
        type=float,
        default=DEFAULT_DEADLINE_SECONDS,
        help=f"seconds before a cold case gives up and reports how far it got "
        f"(default {DEFAULT_DEADLINE_SECONDS:g})",
    )
    args = parser.parse_args(argv)

    cases = build_cases(quick=args.quick, deadline_s=args.deadline)
    if args.case:
        cases = {
            name: case
            for name, case in cases.items()
            if any(needle in name for needle in args.case)
        }
        if not cases:
            print("no cases matched", file=sys.stderr)
            return 2

    with tempfile.TemporaryDirectory(dir=args.workdir, prefix="atom-bench-") as tmp:
        root = Path(tmp)
        env = describe_environment(root)
        print(
            f"atom {env['atom_version']} on {env['system']}/{env['machine']} "
            f"({env['container'] or 'host'}), fsync p50 {env['fsync']['p50_ms']} ms",
            file=sys.stderr,
        )
        results = run(cases, args.repeats, root)

    gates = check_budgets(results)
    payload = {
        "schema": RESULT_SCHEMA,
        "label": args.label,
        "environment": env,
        "settings": {
            "repeats": args.repeats,
            "quick": args.quick,
            "deadline_s": args.deadline,
        },
        "cases": results,
        "gates": gates,
    }
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    print(text, end="")

    failures = [gate for gate in gates if gate["status"] == "FAIL"]
    for gate in failures:
        comparator = "<" if gate["direction"] == "at_least" else ">"
        print(
            f"FAIL {gate['case']}: {gate['metric']}={gate['value']} "
            f"{comparator} {gate['limit']}",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
