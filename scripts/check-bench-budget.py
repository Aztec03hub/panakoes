#!/usr/bin/env python3
"""check-bench-budget.py: aggregate hyperfine JSON, fail if p99 > budget.

Reads every *.json in --results-dir (hyperfine's --export-json output),
computes p50/p95/p99/max in ms per fixture, prints a summary table, and
exits non-zero if any fixture's p99 exceeds --p99-ceiling-ms.

Design reference: docs/design/tool-trace-telemetry.md Section 7 (the bench
gates against 15 ms p99 warm per ADV-HIGH-04). The Python script is
intentional per the Gate-2 Decision Beyond the Brief #5: bash can't cleanly
enforce a multi-fixture p99.

Hyperfine JSON shape (relevant fields):
  {"results": [{"mean": s, "stddev": s, "median": s, "min": s, "max": s,
                "times": [s, s, ...]}]}
All times are in seconds; we multiply by 1000 for ms.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import quantiles


def _pctile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0..100). Linear interpolation."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    # statistics.quantiles needs n >= 2. We use n=100 to get 1% buckets, then
    # pick the right cut. quantiles returns 99 cut points (between each
    # consecutive percentile pair); index p-1 is the p-th percentile.
    cuts = quantiles(s, n=100, method="inclusive")
    idx = max(0, min(98, int(round(p)) - 1))
    return cuts[idx]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-dir", required=True, help="dir of hyperfine *.json")
    p.add_argument(
        "--p99-ceiling-ms",
        type=float,
        default=15.0,
        help="p99 wall-clock ceiling in milliseconds (default 15)",
    )
    args = p.parse_args(argv)

    rdir = Path(args.results_dir)
    if not rdir.is_dir():
        print(f"check-bench: --results-dir {rdir} not a directory", file=sys.stderr)
        return 2
    files = sorted(rdir.glob("*.json"))
    if not files:
        print(f"check-bench: no *.json in {rdir}", file=sys.stderr)
        return 2

    print(f"check-bench: ceiling = {args.p99_ceiling_ms:.2f} ms p99 warm")
    print(f"check-bench: fixtures = {len(files)}")
    print()
    print(f"{'fixture':<40} {'p50 ms':>10} {'p95 ms':>10} {'p99 ms':>10} {'max ms':>10}  status")
    print(f"{'-'*40} {'-'*10} {'-'*10} {'-'*10} {'-'*10}  ------")

    any_fail = False
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{f.stem:<40} parse failed: {exc}")
            any_fail = True
            continue
        results = data.get("results")
        if not isinstance(results, list) or not results:
            print(f"{f.stem:<40} no results array")
            any_fail = True
            continue
        times_s = results[0].get("times")
        if not isinstance(times_s, list) or not times_s:
            print(f"{f.stem:<40} no times array")
            any_fail = True
            continue
        times_ms = [t * 1000.0 for t in times_s]
        p50 = _pctile(times_ms, 50)
        p95 = _pctile(times_ms, 95)
        p99 = _pctile(times_ms, 99)
        mx = max(times_ms)
        status = "OK" if p99 <= args.p99_ceiling_ms else "FAIL"
        if status == "FAIL":
            any_fail = True
        print(
            f"{f.stem:<40} {p50:>10.2f} {p95:>10.2f} {p99:>10.2f} {mx:>10.2f}  {status}"
        )

    print()
    if any_fail:
        print(
            f"check-bench: BUDGET MISS. One or more fixtures exceed {args.p99_ceiling_ms} ms p99.",
            file=sys.stderr,
        )
        print(
            "  Mitigation per design Section 7: collapse jq invocations further,"
            " or rewrite the shim in Python (the documented escape hatch).",
            file=sys.stderr,
        )
        return 1
    print("check-bench: BUDGET OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
