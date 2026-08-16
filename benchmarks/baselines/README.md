# Tokenization engine baselines

Reference runs of `scripts/bench_privacy.py`, one file per host. Compare a new
run against the matching rung — a single run in isolation says little, because
the numbers are dominated by the host:

```bash
python3 -m scripts.bench_privacy --repeats 3 --deadline 45 \
  --label 4-nano-lxc-rpi --out /tmp/new.json
python3 -m scripts.compare_bench \
  benchmarks/baselines/v0.10.5-4-nano-lxc-rpi.json /tmp/new.json
```

Files are named `<version>-<rung>.json`. Keep the old file when adding a new
version: the point of a baseline is the comparison, and overwriting one destroys
the history that makes a regression visible.

Compare against the newest version for a regression check. Compare across
versions only with the case list in mind — v0.10.5 changed what the cold cases
measure, so `mint_rate_1s` is the only minting number that lines up between the
two sets.

## The rungs

Ordered by storage speed, slowest last. Run them one at a time — the Lima VMs
share the host CPU, so a concurrent local run corrupts both sets of numbers.

| Rung | Host | Why it is in the ladder |
| --- | --- | --- |
| 1 | dev machine (macOS, APFS/NVMe) | best case; proves the harness, little else |
| 2 | `atom-debian` Lima VM (ext4) | first honest fsync cost, Linux semantics |
| 3 | incus container inside the `incus` VM | container filesystem layers |
| 4 | `atom` container on `nano` | ARM + flash storage: the revealing rung |

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
