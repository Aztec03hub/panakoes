#!/usr/bin/env bash
# bench-hook.sh: enforce the trace-shim.sh latency budget (15 ms p99 warm).
#
# Runs hyperfine against the shim with each fixture in tests/telemetry/fixtures
# as stdin; exports per-fixture JSON results to bench-results/. Then runs
# scripts/check-bench-budget.py to aggregate and fail non-zero if any
# fixture's p99 exceeds the budget.
#
# Design reference: docs/design/tool-trace-telemetry.md Section 7 (performance
# budget + benchmark) and ADV-HIGH-04 (the 15 ms p99 warm ceiling rationale).
#
# Env:
#   PANAKOES_TELEMETRY_DIR  default /tmp/panakoes-bench (clean per run)
#   BENCH_RUNS              default 200
#   BENCH_WARMUP            default 10
#   P99_CEILING_MS          default 15
#
# Usage: scripts/bench-hook.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
FIXTURE_DIR="$REPO_ROOT/tests/telemetry/fixtures"
SHIM="$REPO_ROOT/.claude/hooks/trace-shim.sh"
RESULTS_DIR="$REPO_ROOT/bench-results"
CHECK="$REPO_ROOT/scripts/check-bench-budget.py"

BENCH_RUNS="${BENCH_RUNS:-200}"
BENCH_WARMUP="${BENCH_WARMUP:-10}"
P99_CEILING_MS="${P99_CEILING_MS:-15}"

# Use an ephemeral spool so we don't pollute the operator's real telemetry.
export PANAKOES_TELEMETRY_DIR="${PANAKOES_TELEMETRY_DIR:-/tmp/panakoes-bench}"
rm -rf "$PANAKOES_TELEMETRY_DIR"
mkdir -p "$PANAKOES_TELEMETRY_DIR"

if [ ! -x "$SHIM" ]; then
  echo "bench-hook: shim not found or not executable: $SHIM" >&2
  exit 2
fi
if ! command -v hyperfine >/dev/null 2>&1; then
  echo "bench-hook: hyperfine not installed; install via 'sudo apt-get install hyperfine' or 'cargo install hyperfine'" >&2
  exit 2
fi

mkdir -p "$RESULTS_DIR"
rm -f "$RESULTS_DIR"/*.json 2>/dev/null || true

echo "bench-hook: budget = $P99_CEILING_MS ms p99 warm"
echo "bench-hook: shim   = $SHIM"
echo "bench-hook: spool  = $PANAKOES_TELEMETRY_DIR"
echo ""

for fixture in "$FIXTURE_DIR"/*.json; do
  name=$(basename "$fixture" .json)
  echo "bench-hook: fixture $name"
  # hyperfine 1.12 (Ubuntu 22.04) does not have --input-file or --shell=none;
  # we use a wrapper command that pipes the fixture via shell stdin redirect.
  hyperfine \
    --warmup "$BENCH_WARMUP" \
    --runs "$BENCH_RUNS" \
    --export-json "$RESULTS_DIR/$name.json" \
    "bash -c '$SHIM < $fixture'" \
    > /dev/null
done

echo ""
echo "bench-hook: aggregating with $CHECK"
python3 "$CHECK" --results-dir "$RESULTS_DIR" --p99-ceiling-ms "$P99_CEILING_MS"
