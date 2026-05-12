#!/usr/bin/env bash
# pr-status.sh: one-line digest of every open PR's queue state.
# Replaces hand-rolled `gh pr list ... | jq` queries with a single command.
#
# Output columns:
#   PR        PR number
#   Merge     mergeStateStatus (CLEAN / BLOCKED / DIRTY / UNSTABLE / UNKNOWN)
#   Mrg?      mergeable (MERGEABLE / CONFLICTING / UNKNOWN)
#   CI        rolled-up status check verdict (pass / fail / running / no-checks)
#   AutoMerge whether auto-merge is armed (Y / -)
#   Labels    comma-joined PR labels
#   Title     first 60 chars of the PR title
#
# Usage:
#   make pr-status
#   scripts/pr-status.sh
#   scripts/pr-status.sh 50      # limit to first 50 (default)
#   scripts/pr-status.sh --me    # only PRs where I'm the author
#
# Exit codes:
#   0  printed status (even if zero PRs open)
#   1  gh CLI not available or not logged in

set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "pr-status: gh CLI not installed" >&2
  exit 1
fi

LIMIT=50
EXTRA_FLAGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --me) EXTRA_FLAGS+=(--author "@me") ;;
    [0-9]*) LIMIT="$1" ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *) echo "pr-status: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

# We pull statusCheckRollup so we can compute a single CI verdict per PR
# rather than asking the user to chase failing checks themselves. The
# rollup contains a heterogeneous mix of CheckRun and StatusContext
# nodes; the conclusion field on a finished check is what we want, with
# status falling back when the check is still in-flight.
gh pr list --state open --limit "$LIMIT" "${EXTRA_FLAGS[@]}" \
  --json number,title,mergeable,mergeStateStatus,isDraft,labels,statusCheckRollup,autoMergeRequest \
  --jq '
    sort_by(.number) | reverse |
    .[] | {
      n:   .number,
      ms:  .mergeStateStatus,
      mg:  .mergeable,
      d:   (if .isDraft then "Y" else "-" end),
      am:  (if (.autoMergeRequest // null) then "Y" else "-" end),
      lbl: ((.labels // []) | map(.name) | join(",")),
      ck: (
        ((.statusCheckRollup // []) | map(.conclusion // .status // "PENDING")) as $rs |
        if ($rs | length) == 0 then "no-checks"
        elif ($rs | any(. == "FAILURE" or . == "TIMED_OUT" or . == "CANCELLED" or . == "ACTION_REQUIRED")) then "fail"
        elif ($rs | any(. == "PENDING" or . == "IN_PROGRESS" or . == "QUEUED")) then "running"
        elif ($rs | all(. == "SUCCESS" or . == "NEUTRAL" or . == "SKIPPED")) then "pass"
        else "mixed" end
      ),
      t: (.title[0:60])
    } | "\(.n)\t\(.ms)\t\(.mg)\t\(.ck)\t\(.am)\t\(.lbl)\t\(.t)"
  ' | (printf "PR\tMerge\tMrg?\tCI\tAuto\tLabels\tTitle\n"; cat) \
    | column -t -s$'\t'
