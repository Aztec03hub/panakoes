#!/usr/bin/env bash
# wait-for-pr.sh: wait until one or more PRs reach a resolved state.
#
# Single-PR mode (one positional arg): wait until that PR is ready to merge,
# fails a required check, or times out.
#
# Multi-PR mode (>1 positional args): wait until the chosen condition is met
# across the set. Default mode is --all (every PR ready or one failed).
# --any exits as soon as any PR is ready or any PR fails.
#
# Designed to replace ad-hoc `until` loops the orchestrator was writing
# inline. Always has a well-defined exit condition so the loop terminates.
#
# Exit codes:
#   0  ready to merge (single: this PR; --any: at least one; --all: every PR)
#   1  required-check failed (single: this PR; --any: at least one; --all: any)
#   2  timeout reached without resolution
#   3  every supplied PR is already CLOSED / MERGED before any decision
#   4  bad usage / dependency missing
#
# Usage: wait-for-pr.sh <PR_NUMBER> [<PR_NUMBER> ...] [options]
#
# Options:
#   --any                Multi-PR mode: exit on the FIRST PR that resolves
#                        (ready OR failed). Default for >1 PRs is --all.
#   --all                Multi-PR mode: wait for every PR (default).
#   --required PATTERN   Extended regex matching required-check names.
#   --timeout  SECONDS   Max time to wait (default 600).
#   --interval SECONDS   Polling interval (default 30).
#   --auto-update        When mergeStateStatus is BEHIND, call update-branch
#                        and continue waiting. Throttled to one call per
#                        --interval per PR.
#   --quiet              Only print final status and exit.
#   -h, --help           Show help.
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
# - Per iteration, costs ~2 gh API requests per PR. For multi-PR, stay
#   mindful of the rate limit (5000/hr authenticated).
# - "BLOCKED" + 0 required failures means CI is still running. The script
#   waits. "UNKNOWN" is a transient state GitHub uses while it recomputes
#   mergeability after a force-push or merge; the script also waits on this.
# - With --auto-update, BEHIND triggers a single update-branch call per
#   iteration per PR. The script does NOT spin-call update-branch.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults

DEFAULT_REQUIRED='Terraform fmt|Scan for secrets|Analyze \(actions\)|Verify CHANGELOG|Python tests gate|TypeScript tests gate'
DEFAULT_TIMEOUT=600
DEFAULT_INTERVAL=30
QUIET=0
AUTO_UPDATE=0
MODE=""   # "any" or "all"; auto-defaults below

# ---------------------------------------------------------------------------
# Arg parse

usage() {
  cat <<EOF
Usage: $(basename "$0") <PR> [<PR> ...] [options]

Single PR (1 positional): wait for it to resolve.
Multi PR (>1 positional): wait under --all (default) or --any.

Options:
  --any                Exit on first PR that resolves (multi-PR only).
  --all                Wait for every PR to resolve (multi-PR default).
  --required PATTERN   Extended regex matching required-check names.
                       Default: ${DEFAULT_REQUIRED}
  --timeout  SECONDS   Max time to wait (default ${DEFAULT_TIMEOUT}).
  --interval SECONDS   Polling interval (default ${DEFAULT_INTERVAL}).
  --auto-update        Call update-branch on BEHIND PRs each tick.
  --quiet              Only print final status.
  -h, --help           This help.

Exit codes:
  0  ready to merge
  1  required-check failed
  2  timeout
  3  PR(s) already closed
  4  bad usage
EOF
}

if [ $# -lt 1 ]; then usage >&2; exit 4; fi

PRS=()
REQUIRED="$DEFAULT_REQUIRED"
TIMEOUT="$DEFAULT_TIMEOUT"
INTERVAL="$DEFAULT_INTERVAL"

while [ $# -gt 0 ]; do
  case "$1" in
    --required)    REQUIRED="$2";    shift 2 ;;
    --timeout)     TIMEOUT="$2";     shift 2 ;;
    --interval)    INTERVAL="$2";    shift 2 ;;
    --auto-update) AUTO_UPDATE=1;    shift ;;
    --quiet)       QUIET=1;          shift ;;
    --any)         MODE="any";       shift ;;
    --all)         MODE="all";       shift ;;
    -h|--help)     usage;            exit 0 ;;
    --*) echo "unknown flag: $1" >&2; usage >&2; exit 4 ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then PRS+=("$1"); shift
      else echo "wait-for-pr: PR numbers must be numeric, got '$1'" >&2; exit 4
      fi
      ;;
  esac
done

if [ "${#PRS[@]}" -eq 0 ]; then echo "missing PR number" >&2; usage >&2; exit 4; fi
if ! command -v gh >/dev/null; then
  echo "wait-for-pr: 'gh' CLI not on PATH" >&2; exit 4
fi
if ! command -v jq >/dev/null; then
  echo "wait-for-pr: 'jq' not on PATH" >&2; exit 4
fi

# Default mode: --all when >1 PRs, single-PR when 1.
if [ "${#PRS[@]}" -eq 1 ]; then
  MODE="single"
elif [ -z "$MODE" ]; then
  MODE="all"
fi

# ---------------------------------------------------------------------------
# Helpers

log() {
  if [ "$QUIET" = "0" ]; then
    printf '  %s %s\n' "$(date +%H:%M:%S)" "$*"
  fi
}

# repo (owner/name) for the update-branch endpoint, derived once.
REPO=""
if [ "$AUTO_UPDATE" = "1" ]; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
  if [ -z "$REPO" ]; then
    echo "wait-for-pr: --auto-update needs gh to resolve current repo (cwd inside a git checkout?)" >&2
    exit 4
  fi
fi

# query_pr <pr> -> echoes "pr_state|merge_state|req_fails|pending|fail_names"
query_pr() {
  local pr="$1"
  local pr_json checks_raw pr_state merge_state req_fails pending fail_names
  pr_json=$(gh pr view "$pr" --json state,mergeStateStatus 2>/dev/null || echo '{}')
  pr_state=$(printf '%s' "$pr_json" | jq -r '.state // "ERR"')
  merge_state=$(printf '%s' "$pr_json" | jq -r '.mergeStateStatus // "ERR"')

  checks_raw=$(gh pr checks "$pr" 2>/dev/null || true)
  req_fails=$(printf '%s\n' "$checks_raw" | awk -F'\t' '$2=="fail"' | grep -Ec "$REQUIRED" || true)
  pending=$(printf '%s\n' "$checks_raw" | awk -F'\t' '$2=="pending"' | wc -l)
  fail_names=$(printf '%s\n' "$checks_raw" | awk -F'\t' '$2=="fail"' | grep -E "$REQUIRED" | awk -F'\t' '{print $1}' | paste -sd',' -)

  printf '%s|%s|%s|%s|%s\n' "$pr_state" "$merge_state" "$req_fails" "$pending" "$fail_names"
}

# resolved_for <pr_state> <merge_state> <req_fails> -> "ready" | "failed" | "closed" | "wait"
resolved_for() {
  local pr_state="$1" merge_state="$2" req_fails="$3"
  if [ "$pr_state" = "CLOSED" ] || [ "$pr_state" = "MERGED" ]; then
    echo "closed"; return
  fi
  if [ "$merge_state" = "CLEAN" ] || [ "$merge_state" = "MERGEABLE" ]; then
    echo "ready"; return
  fi
  if [ "$req_fails" -gt 0 ]; then
    echo "failed"; return
  fi
  echo "wait"
}

# ---------------------------------------------------------------------------
# Main loop

START=$(date +%s)
elapsed=0
# Per-PR state captured each iteration. Bash 4+ associative arrays.
declare -A LAST_PR_STATE LAST_MERGE LAST_REQ_FAILS LAST_PENDING LAST_FAIL_NAMES LAST_RESOLUTION

# loop_done returns 0 when we should exit the loop, 1 to keep waiting.
loop_done() {
  local any_ready=0 any_failed=0 all_resolved=1 any_resolved=0
  for pr in "${PRS[@]}"; do
    case "${LAST_RESOLUTION[$pr]:-wait}" in
      ready)  any_ready=1;  any_resolved=1 ;;
      failed) any_failed=1; any_resolved=1 ;;
      closed) any_resolved=1 ;;
      wait)   all_resolved=0 ;;
    esac
  done

  if [ "$elapsed" -gt "$TIMEOUT" ]; then return 0; fi

  case "$MODE" in
    single|any)
      # Exit as soon as ANY PR reaches a terminal state. "closed" (merged or
      # closed-without-merge) counts: if I'm waiting on a queue and one
      # merges, that IS the signal. Without this, --any silently waits past
      # auto-merges that fire during the watch.
      [ "$any_resolved" = "1" ] && return 0
      ;;
    all)
      [ "$any_failed" = "1" ] && return 0
      [ "$all_resolved" = "1" ] && return 0
      ;;
  esac
  return 1
}

iter() {
  local pr line pr_state merge_state req_fails pending fail_names res
  for pr in "${PRS[@]}"; do
    line=$(query_pr "$pr")
    pr_state=$(  printf '%s' "$line" | cut -d'|' -f1)
    merge_state=$(printf '%s' "$line" | cut -d'|' -f2)
    req_fails=$( printf '%s' "$line" | cut -d'|' -f3)
    pending=$(   printf '%s' "$line" | cut -d'|' -f4)
    fail_names=$(printf '%s' "$line" | cut -d'|' -f5)
    res=$(resolved_for "$pr_state" "$merge_state" "$req_fails")

    LAST_PR_STATE[$pr]="$pr_state"
    LAST_MERGE[$pr]="$merge_state"
    LAST_REQ_FAILS[$pr]="$req_fails"
    LAST_PENDING[$pr]="$pending"
    LAST_FAIL_NAMES[$pr]="$fail_names"
    LAST_RESOLUTION[$pr]="$res"

    if [ "$AUTO_UPDATE" = "1" ] && [ "$merge_state" = "BEHIND" ]; then
      log "  auto-update #$pr (BEHIND): calling update-branch"
      gh api -X PUT "repos/${REPO}/pulls/${pr}/update-branch" >/dev/null 2>&1 \
        && log "    dispatched" \
        || log "    update-branch failed (may be a real conflict; retry next tick)"
    fi

    if [ -n "$fail_names" ]; then
      log "#$pr pr=$pr_state merge=$merge_state pending=$pending req-fail=[$fail_names] resolution=$res"
    else
      log "#$pr pr=$pr_state merge=$merge_state pending=$pending req-fails=0 resolution=$res"
    fi
  done
}

log "watching PRs: ${PRS[*]} | mode=$MODE | timeout=${TIMEOUT}s | interval=${INTERVAL}s$([ "$AUTO_UPDATE" = "1" ] && echo ' | auto-update=on')"

while true; do
  iter
  elapsed=$(($(date +%s) - START))
  if loop_done; then break; fi
  sleep "$INTERVAL"
done

# ---------------------------------------------------------------------------
# Exit dispatch

# Find the first PR that resolved as failed (priority 1), then ready, then closed.
first_failed=""
first_ready=""
all_closed=1
for pr in "${PRS[@]}"; do
  case "${LAST_RESOLUTION[$pr]:-wait}" in
    failed) [ -z "$first_failed" ] && first_failed="$pr"; all_closed=0 ;;
    ready)  [ -z "$first_ready" ] && first_ready="$pr"; all_closed=0 ;;
    closed) ;;
    *)      all_closed=0 ;;
  esac
done

if [ "$elapsed" -gt "$TIMEOUT" ]; then
  log "TIMEOUT after ${elapsed}s. Resolutions: $(for pr in "${PRS[@]}"; do printf '#%s=%s ' "$pr" "${LAST_RESOLUTION[$pr]:-wait}"; done)"
  exit 2
fi

# Mode-aware exit.
case "$MODE" in
  single|any)
    if [ -n "$first_failed" ]; then
      log "FAILED #$first_failed: required-check failures = [${LAST_FAIL_NAMES[$first_failed]}] after ${elapsed}s."
      exit 1
    fi
    if [ -n "$first_ready" ]; then
      log "READY #$first_ready (state=${LAST_MERGE[$first_ready]}) after ${elapsed}s."
      exit 0
    fi
    if [ "$all_closed" = "1" ]; then
      log "All supplied PRs are CLOSED/MERGED."
      exit 3
    fi
    ;;
  all)
    if [ -n "$first_failed" ]; then
      log "FAILED #$first_failed: required-check failures = [${LAST_FAIL_NAMES[$first_failed]}] after ${elapsed}s."
      exit 1
    fi
    # all-mode reaches here only when every PR is ready or closed.
    log "ALL PRs resolved after ${elapsed}s. Resolutions: $(for pr in "${PRS[@]}"; do printf '#%s=%s ' "$pr" "${LAST_RESOLUTION[$pr]}"; done)"
    if [ "$all_closed" = "1" ]; then exit 3; fi
    exit 0
    ;;
esac

# Defensive fallback (should not reach here).
log "Loop exited in indeterminate state after ${elapsed}s."
exit 2
