"""Diff two ``bench_privacy`` runs.

The interesting output of the ladder is never one run: it is "the Pi is 40x
slower on cold minting and 1.1x on the warm path", which says the storage is the
variable rather than the CPU. Neither run alone shows that.

    python3 -m scripts.compare_bench local.json nano.json
    python3 -m scripts.compare_bench local.json nano.json --metric p99_ms

Reads the JSON ``bench_privacy --out`` writes. Refuses to diff runs whose schema
differs, since lining up columns that mean different things is worse than
refusing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: Ratios above this are called out. Flash storage against APFS is expected to
#: be several times slower; an order of magnitude is a finding.
NOTABLE_RATIO = 3.0

#: Durations at or below this are reported without a ratio. Both sides of such a
#: comparison mean "too fast to time", and dividing one by the other manufactures
#: a large number from timer noise.
TIMER_FLOOR_MS = 0.01


def load(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def describe(run: dict[str, Any], path: Path) -> str:
    env = run.get("environment", {})
    label = run.get("label") or path.stem
    fsync = env.get("fsync", {}).get("p50_ms")
    return (
        f"{label} — atom {env.get('atom_version')} on "
        f"{env.get('system')}/{env.get('machine')} "
        f"({env.get('container') or 'host'}), fsync p50 {fsync} ms"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--metric",
        default="total_ms",
        help="which summary field to compare (default total_ms)",
    )
    args = parser.parse_args(argv)

    base = load(args.baseline)
    cand = load(args.candidate)
    if base.get("schema") != cand.get("schema"):
        print(
            f"schema mismatch: {base.get('schema')} vs {cand.get('schema')}; "
            "re-run both rungs with the same version",
            file=sys.stderr,
        )
        return 2

    print(f"baseline   {describe(base, args.baseline)}")
    print(f"candidate  {describe(cand, args.candidate)}")
    base_fsync = base["environment"]["fsync"]["p50_ms"]
    cand_fsync = cand["environment"]["fsync"]["p50_ms"]
    if base_fsync:
        print(f"\nfsync p50 ratio: {cand_fsync / base_fsync:.1f}x "
              f"({base_fsync} -> {cand_fsync} ms)")

    base_cases: dict[str, Any] = base.get("cases", {})
    cand_cases: dict[str, Any] = cand.get("cases", {})
    shared = [name for name in base_cases if name in cand_cases]
    only_base = sorted(set(base_cases) - set(cand_cases))
    only_cand = sorted(set(cand_cases) - set(base_cases))

    metric = args.metric
    print(f"\n{'case':<28} {'baseline':>12} {'candidate':>12} {'ratio':>9}   fsync")
    print("-" * 76)
    notable: list[tuple[str, float]] = []
    for name in shared:
        before = base_cases[name].get(metric)
        after = cand_cases[name].get(metric)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        syncs = cand_cases[name].get("fsyncs", 0)
        # A duration at or below timer resolution cannot carry a ratio: dividing
        # by it reports "15x" or "infx" for two measurements that both mean "too
        # fast to time". `flag_off` is the case that exposed this — it measures a
        # no-op on purpose.
        if before < TIMER_FLOOR_MS or after < TIMER_FLOOR_MS:
            print(
                f"{name:<28} {before:>12.3f} {after:>12.3f} {'--':>9} "
                f"{syncs:>7}   below timer resolution"
            )
            continue
        ratio = after / before
        flag = "  <--" if ratio >= NOTABLE_RATIO else ""
        print(
            f"{name:<28} {before:>12.3f} {after:>12.3f} {ratio:>8.1f}x "
            f"{syncs:>7}{flag}"
        )
        if ratio >= NOTABLE_RATIO:
            notable.append((name, ratio))

    if only_base or only_cand:
        print()
        if only_base:
            print(f"only in baseline:  {', '.join(only_base)}")
        if only_cand:
            print(f"only in candidate: {', '.join(only_cand)}")

    print()
    for run, path in ((base, args.baseline), (cand, args.candidate)):
        failures = [g for g in run.get("gates", []) if g.get("status") == "FAIL"]
        if failures:
            # The comparator has to follow the gate's direction: a throughput
            # floor fails by being under its limit, and printing ">" for it
            # states the opposite of what happened.
            names = ", ".join(
                f"{g['case']} ({g['value']} "
                f"{'<' if g.get('direction') == 'at_least' else '>'} {g['limit']})"
                for g in failures
            )
            print(f"gates failed in {path.stem}: {names}")
        else:
            print(f"gates passed in {path.stem}")

    if notable:
        print(f"\n{len(notable)} case(s) at or above {NOTABLE_RATIO}x:")
        for name, ratio in sorted(notable, key=lambda item: -item[1]):
            print(f"  {name:<28} {ratio:>6.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
