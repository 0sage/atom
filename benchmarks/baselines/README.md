# Tokenization engine baselines

Reference runs of `scripts/bench_privacy.py`, one file per host. Compare a new
run against the matching rung — a single run in isolation says little, because
the numbers are dominated by the host:

```bash
python3 -m scripts.bench_privacy --repeats 3 --deadline 45 \
  --label 4-nano-lxc-rpi --out /tmp/new.json
python3 -m scripts.compare_bench \
  benchmarks/baselines/v0.10.3-4-nano-lxc-rpi.json /tmp/new.json
```

Files are named `<version>-<rung>.json`. Keep the old file when adding a new
version: the point of a baseline is the comparison, and overwriting one destroys
the history that makes a regression visible.

## The rungs

Ordered by storage speed, slowest last. Run them one at a time — the Lima VMs
share the host CPU, so a concurrent local run corrupts both sets of numbers.

| Rung | Host | Why it is in the ladder |
| --- | --- | --- |
| 1 | dev machine (macOS, APFS/NVMe) | best case; proves the harness, little else |
| 2 | `atom-debian` Lima VM (ext4) | first honest fsync cost, Linux semantics |
| 3 | incus container inside the `incus` VM | container filesystem layers |
| 4 | `atom` container on `nano` | ARM + flash storage: the revealing rung |

## v0.10.3 — the first full run (2026-08-16)

Throughput, and the ~9x spread between the fastest and slowest rung:

| case | Pi (LXC) | incus-in-VM | lima VM | macOS |
| --- | --- | --- | --- | --- |
| `mint_rate_1s` | 470/s | 694/s | 713/s | 814/s |
| `warm_rate_1s` | 124,830/s | 1.30M/s | 1.31M/s | 1.26M/s |
| `detokenize_rate_1s` | 43,853/s | 746,300/s | 739,680/s | 404,642/s |

Two things to read out of that table:

**Minting is ~1700x slower than the warm path** on the same call. `_save`
reserializes the whole map for every new value, so the cost is quadratic in the
number of new addresses — see `.agent/privacy.md`. `tool_result_cold_10000`
aborted at its deadline on *every* rung, including the fastest.

**The Pi is only ~1.7x slower at minting but ~10x slower warm.** Minting is
bound by serialization, which the ARM CPU does at a broadly similar rate; the
warm path is bound by CPU and shows the real hardware gap. A change that helps
one may do nothing for the other.

`environment.fsync` in each file records a bare fsync on that filesystem, so a
slow rung is attributable rather than mysterious. Note that the Pi's is *fastest*
(0.0015ms) — its container does not honour the flush — which is the second reason
fsync is not what makes minting expensive.

Rung 4 runs Python 3.14 against 3.11/3.12 on the others, so its interpreter is a
confound in any cross-rung comparison; within-rung comparisons against this
baseline are unaffected.
