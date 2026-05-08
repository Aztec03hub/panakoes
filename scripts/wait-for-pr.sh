#!/usr/bin/env bash
# wait-for-pr.sh: wait until a PR is ready to merge, fails a required check,
# or hits a timeout.
#
# Designed to replace ad-hoc `until` loops the orchestrator was writing
# inline. Always has a well-defined exit condition so the loop terminates.
#
# Exit codes:
#   0  PR ready to merge (mergeStateStatus is CLEAN or MERGEABLE)
#   1  one or more required status checks FAILED (real problem)
#   2  timeout reached without resolution
#   3  PR closed (already merged or closed-without-merge)
#   4  bad usage / dependency missing
#
# Usage: wait-for-pr.sh <PR_NUMBER> [--required PATTERN] [--timeout SEC] \
#                        [--interval SEC] [--quiet]
#
# Default required-check pattern matches Panakoes' rulesets:
#   Terraform fmt, Scan for secrets, Analyze (actions),
#   Verify CHANGELOG, Python tests gate, TypeScript tests gate.
#
# Override with --required '<extended-regex>' for other repos / different
# rulesets. The pattern is passed verbatim to grep -E.
#
# Caveats:
# - Does NOT poll GitHub more frequently than --interval; defaults to 30s.
# - Per call, costs 2 gh API requests. Stay under the rate limit.
# - "BLOCKED" + 0 required failures means CI is still running. The script
#   waits. "UNKNOWN" is a transient state GitHub uses while it recomputes
#   mergeability after a force-push or merge; the script also waits on this.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults

DEFAULT_REQUIRED='Terraform fmt|Scan for secrets|Analyze \(actions\)|Verify CHANGELOG|Python tests gate|TypeScript tests gate'
DEFAULT_TIMEOUT=600          # 10 minutes upper bound
DEFAULT_INTERVAL=30
QUIET=0

# ---------------------------------------------------------------------------
# Arg parse

usage() {
  cat <<EOF
Usage: $(basename "$0") <PR_NUMBER> [options]

Options:
  --required PATTERN  Extended regex matching required-check names.
                      Default: ${DEFAULT_REQUIRED}
  --timeout  SECONDS  Max time to wait (default ${DEFAULT_TIMEOUT}).
  --interval SECONDS  Polling interval (default ${DEFAULT_INTERVAL}).
  --quiet             Only print final status and exit.
  -h, --help          This help.

Exit codes:
  0  ready to merge
  1  required-check failed
  2  timeout
  3  PR already closed
  4  bad usage
EOF
}

if [ $# -lt 1 ]; then usage >&2; exit 4; fi

PR=""
REQUIRED="$DEFAULT_REQUIRED"
TIMEOUT="$DEFAULT_TIMEOUT"
INTERVAL="$DEFAULT_INTERVAL"

while [ $# -gt 0 ]; do
  case "$1" in
    --required) REQUIRED="$2"; shift 2 ;;
    --timeout)  TIMEOUT="$2";  shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --quiet)    QUIET=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    --*) echo "unknown flag: $1" >&2; usage >&2; exit 4 ;;
    *)
      if [ -z "$PR" ]; then PR="$1"; shift
      else echo "unexpected positional arg: $1" >&2; exit 4
      fi
      ;;
  esac
done

if [ -z "$PR" ]; then echo "missing PR number" >&2; usage >&2; exit 4; fi
if ! command -v gh >/dev/null; then
  echo "wait-for-pr: 'gh' CLI not on PATH" >&2; exit 4
fi
if ! [[ "$PR" =~ ^[0-9]+$ ]]; then
  echo "wait-for-pr: PR must be numeric, got '$PR'" >&2; exit 4
fi

# ---------------------------------------------------------------------------
# Helpers

log() {
  if [ "$QUIET" = "0" ]; then
    printf '  %s %s\n' "$(date +%H:%M:%S)" "$*"
  fi
}

START=$(date +%s)

# ---------------------------------------------------------------------------
# Loop

# The until's body queries gh and updates `state` and `req_fails` on
# every iteration. The condition exits when:
#   1. PR closed (CLOSED / MERGED state).
#   2. PR is mergeable (state in {CLEAN, MERGEABLE}); auto-merge will fire.
#   3. A required-check failed (req_fails >= 1).
#   4. Timeout reached.
# Each iteration is bounded; the loop is guaranteed to terminate.

state=""
req_fails=0
elapsed=0
pr_state=""

until
  pr_state=$(gh pr view "$PR" --json state -q .state 2>/dev/null || echo "ERR")
  state=$(gh pr view "$PR" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null || echo "ERR")
  req_fails=$(gh pr checks "$PR" 2>/dev/null \
              | awk -F'\t' '$2=="fail"' \
              | grep -Ec "$REQUIRED" || true)
  elapsed=$(($(date +%s) - START))
  log "pr=$pr_state merge=$state req-fails=$req_fails elapsed=${elapsed}s"
  [ "$pr_state" = "CLOSED" ] || [ "$pr_state" = "MERGED" ] \
    || [ "$state" = "CLEAN" ] || [ "$state" = "MERGEABLE" ] \
    || [ "$req_fails" -gt 0 ] \
    || [ "$elapsed" -gt "$TIMEOUT" ]
do
  sleep "$INTERVAL"
done

# ---------------------------------------------------------------------------
# Exit dispatch

if [ "$pr_state" = "CLOSED" ] || [ "$pr_state" = "MERGED" ]; then
  log "PR #$PR is $pr_state."
  exit 3
fi
if [ "$state" = "CLEAN" ] || [ "$state" = "MERGEABLE" ]; then
  log "PR #$PR ready to merge (state=$state) after ${elapsed}s."
  exit 0
fi
if [ "$req_fails" -gt 0 ]; then
  log "PR #$PR has $req_fails failing REQUIRED check(s) after ${elapsed}s."
  if [ "$QUIET" = "0" ]; then
    gh pr checks "$PR" 2>/dev/null | awk -F'\t' '$2=="fail"' | grep -E "$REQUIRED" || true
  fi
  exit 1
fi
log "PR #$PR timed out after ${elapsed}s without resolution (state=$state)."
exit 2
