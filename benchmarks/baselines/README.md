# Tokenization engine baselines

Reference runs of `scripts/bench_privacy.py`, one file per host. Compare a new
run against the matching rung — a single run in isolation says little, because
the numbers are dominated by the host:

```bash
python3 -m scripts.bench_privacy --repeats 3 --deadline 45 \
  --label 4-nano-lxc-rpi --out /tmp/new.json
python3 -m scripts.compare_bench \
  benchmarks/baselines/v0.11.2-4-nano-lxc-rpi.json /tmp/new.json
```

Files are named `<version>-<rung>.json`. Keep the old file when adding a new
version: the point of a baseline is the comparison, and overwriting one destroys
the history that makes a regression visible.

Compare against the newest version for a regression check. Compare across
versions only with the case list in mind — v0.10.5 changed what the cold cases
measure, so `mint_rate_1s` is the only minting number that lines up between the
two sets. Cases were also added later than the runs beneath them: `mask_*` first
appear in v0.11.1 and `no_match_at_map_size_*` in v0.11.2, so `compare_bench`
reports them as "only in candidate" against an older baseline. That is the tool
lining up what it can, not a failure.

## The rungs

Ordered by storage speed, slowest last. Run them one at a time — the Lima VMs
share the host CPU, so a concurrent local run corrupts both sets of numbers.

| Rung | Host | Why it is in the ladder |
| --- | --- | --- |
| 1 | dev machine (macOS, APFS/NVMe) | best case; proves the harness, little else |
| 2 | `atom-debian` Lima VM (ext4) | first honest fsync cost, Linux semantics |
| 3 | incus container inside the `incus` VM | container filesystem layers |
| 4 | `atom` container on `nano` | ARM + flash storage: the revealing rung |

## v0.11.1 / v0.11.2 — the two defects behind declared masks (2026-08-16)

v0.11.0 added `/mask`, and the run that followed found two performance defects in
it. Both baselines are kept: v0.11.1 fixes the first and is the run that exposed
the second.

**v0.11.1 — the hit path was never measured.** `mask_registry_*` runs against text
no mask appears in, where the alternation's own prefilter returns early, so it was
flat by construction and could not fail. On a hit, `token_for_mask` resolved the
matched text by scanning the whole map and casefolding every entry:

| registry | v0.11.0 | v0.11.1 |
| --- | --- | --- |
| 1 mask | 2,108,370 hits/s | 2,118,274 hits/s |
| 1000 masks | 113,469 hits/s | 996,056 hits/s |

A casefolded-value index replaced the scan. The residual slope is the 1000-branch
regex itself (0.115 → 0.290 ms for the match alone), not the lookup. Egress was
flat throughout and unchanged — detokenization keys on the placeholder, so ~1.8–1.9M
tokens/s at every registry and map size. `mask_hits_*` now runs at 1/100/1000 with
its own gate, and its corpus indexes modulo the registry size: hardcoding two names
measured nothing at count=1, where neither is registered.

**v0.11.2 — the ladder itself found the second one, and it was worse.** Across all
four rungs, `no_match_baseline` moved 1.5–1.9× against v0.10.6:

| rung | v0.10.6 | v0.11.1 | v0.11.2 |
| --- | --- | --- | --- |
| macOS | 0.0006 ms | 0.0009 ms | 0.0007 ms |
| lima VM | 0.0005 ms | 0.0008 ms | 0.0006 ms |
| incus-in-VM | 0.0004 ms | 0.0008 ms | 0.0006 ms |
| Pi (LXC) | 0.0041 ms | 0.0094 ms | 0.0054 ms |

300 nanoseconds against a 0.1 ms gate, so nothing failed and the magnitude looked
like nothing. **What made it worth chasing is that it moved identically on every
rung** — noise does not do that. The cause: `mask_pattern` is called once per
`tokenize` and decided whether its cached regex was stale by building a signature
over every map entry. So the gate was O(map size) on every message, *including for
operators who have never run `/mask`* — 0.38µs at an empty map against 141µs at
10,000 entries. A dirty flag replaced it; flat at 0.21µs across map sizes.

`no_match_baseline` could not catch this because it runs against an *empty* map. The
configuration that matters is a populated map with no masks — the common one — now
covered by `no_match_at_map_size_{0,1000,10000}`, gated at 10,000 and measuring
0.0004 ms flat on rungs 1–3 and 0.0040 ms flat on the Pi.

The generalizable lesson, which cost two releases: **a prefilter makes the miss path
flat by construction.** Measuring only that says nothing about the hit path, and
nothing about whether checking the gate is itself the expensive part.

## v0.10.6 — cached timestamp on the egress path (2026-08-16)

`_utc_now` caches the second it describes. Profiling the Pi's weak egress number
found `_touch` was 87% of `detokenize`, and `strftime` was 94% of that — the same
second-resolution string formatted once per placeholder. No disk was involved:
100k resolutions wrote the map zero times, which disproved the earlier guess that
the throttled flush was to blame.

| case | Pi (LXC) | incus-in-VM | lima VM | macOS |
| --- | --- | --- | --- | --- |
| `detokenize_rate_1s` | 157,870/s | 1.84M/s | 1.85M/s | 1.85M/s |
| — vs v0.10.5 | 3.5× | 2.5× | 2.5× | 3.5× |
| `detokenize_10000` | 259 ms | 43 ms | 43 ms | 42 ms |

`mint_rate_bulk_1s` also improved (Pi 22,725/s → 39,118/s) because minting stamps
`created` and `last_used` per entry and paid the same cost. The warm path is flat,
as expected — it never touches a timestamp.

## v0.10.5 — batched minting (2026-08-16)

`tokenize` now wraps its substitutions in `TokenStore.minting_batch`, so one call
costs one save however many new addresses it carries.

A bulk tool result carrying 10,000 new addresses — the case `MAX_ENTRIES` exists
for. At v0.10.3 this hit its deadline on every rung, fastest included:

| rung | v0.10.3 | v0.10.5 |
| --- | --- | --- |
| macOS | aborted at 6,000/10,000 | 60 ms (166,590/s) |
| lima VM | aborted at 5,825/10,000 | 53 ms (190,472/s) |
| incus-in-VM | aborted at 5,675/10,000 | 54 ms (183,808/s) |
| Pi (LXC) | aborted at 3,800/10,000 | **431 ms** (23,216/s) |

The warm and egress paths are unchanged, as expected — a repeat hits the
in-memory index and never wrote anything, batched or not:

| case | Pi (LXC) | incus-in-VM | lima VM | macOS |
| --- | --- | --- | --- | --- |
| `mint_rate_bulk_1s` | 22,725/s | 186,140/s | 189,918/s | 169,240/s |
| `mint_rate_1s` | 2,629/s | 4,139/s | 4,155/s | 4,335/s |
| `warm_rate_1s` | 124,437/s | 1.29M/s | 1.31M/s | 1.24M/s |
| `detokenize_rate_1s` | 44,932/s | 730,057/s | 738,078/s | 528,308/s |

**Minting appears twice on purpose, and the two differ by ~40x.** Batching
collapses saves *within* a call and cannot collapse them across calls, so the
shape of the traffic decides which number applies: `mint_rate_bulk_1s` is one
call carrying everything, `mint_rate_1s` fills its budget with 25-address calls.
Both are real — one bulk tool result versus a chatty stream of small ones — and
reporting either alone misstates the engine. The gap is the argument for any
future cross-call coalescing.

This is also the trap that produced a wrong number during development: the
v0.10.3 cold cases sliced their corpus to check a deadline clock, which after
batching measured the harness's own call boundaries rather than the engine, and
understated the gain as 5.6x. `case_tool_result_cold` now passes the corpus in one
call and `case_mint_drip` measures the sliced shape deliberately.

## v0.10.3 — the first full run (2026-08-16)

Throughput before batching, and the ~9x spread between fastest and slowest rung:

| case | Pi (LXC) | incus-in-VM | lima VM | macOS |
| --- | --- | --- | --- | --- |
| `mint_rate_1s` | 470/s | 694/s | 713/s | 814/s |
| `warm_rate_1s` | 124,830/s | 1.30M/s | 1.31M/s | 1.26M/s |
| `detokenize_rate_1s` | 43,853/s | 746,300/s | 739,680/s | 404,642/s |

**Minting was ~1700x slower than the warm path** on the same call, because
`_save` reserialized the whole map for every new value — quadratic in the number
of new addresses. See `.agent/privacy.md`.

**The Pi was only ~1.7x slower at minting but ~10x slower warm.** Minting was
bound by serialization, which the ARM CPU does at a broadly similar rate; the warm
path is bound by CPU and shows the real hardware gap. A change that helps one may
do nothing for the other — which is why both are still reported separately.

## Reading the environment block

`environment.fsync` records a bare fsync on that filesystem, so a slow rung is
attributable rather than mysterious. The Pi's is the *fastest* measured
(0.0015ms) — its container does not honour the flush — which was the second piece
of evidence that fsync is not what made minting expensive.

Rung 4 runs Python 3.14 against 3.11/3.12 on the others, so its interpreter is a
confound in any cross-rung comparison; within-rung comparisons against a baseline
are unaffected.
