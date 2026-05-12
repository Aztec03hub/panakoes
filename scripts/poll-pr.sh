#!/usr/bin/env bash
# Wait for a GitHub PR to reach a terminal state (merged, or any required
# check failed with nothing else pending). Designed for the
# "follow one PR through to merge" workflow.
#
# Usage:
#   scripts/poll-pr.sh <pr-number> [interval-seconds]
#
# Exit: 0 always. Prints one of:
#   DONE: MERGED|<failing-count>|<pending-count>
#   DONE: OPEN|<failing-count>|<pending-count>
#
# Run in the background and watch for the single output line.
#
# Termination logic: exit when EITHER (1) state == MERGED, or
# (2) any check has conclusion == FAILURE AND no checks are
# IN_PROGRESS or QUEUED. The pending == 0 clause is what avoids
# false-negative exits on non-required check failures whose siblings
# are still running.

set -euo pipefail

pr="${1:?pr number required}"
interval="${2:-15}"

until snapshot=$(
  gh pr view "$pr" --json state,statusCheckRollup --jq '
    "\(.state)|\(
      [.statusCheckRollup[]|select(.conclusion=="FAILURE")|.name]|length
    )|\(
      [.statusCheckRollup[]|select(.status=="IN_PROGRESS" or .status=="QUEUED")|.name]|length
    )"
  '
); st=${snapshot%%|*}; rest=${snapshot#*|}; fail=${rest%%|*}; pend=${rest##*|};
  [ "$st" = "MERGED" ] || { [ "$fail" != "0" ] && [ "$pend" = "0" ]; }; do
  sleep "$interval"
done
echo "DONE: $snapshot"
