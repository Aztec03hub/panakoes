#!/usr/bin/env bash
#
# branch-prune.sh -- delete local branches whose remote PRs are MERGED or CLOSED.
#
# Squash-merged PRs leave dangling local branches because `git branch --merged`
# only sees reachability, not PR state. This script asks GitHub directly via
# `gh pr list --state all --head <branch>` for each local branch and deletes
# the ones whose PR is in a terminal state.
#
# Branches with NO PR (state=NONE) or with an OPEN PR are kept. The current
# checked-out branch is always kept (git rejects deleting it). main is always
# kept regardless.
#
# Usage:
#   scripts/branch-prune.sh           # apply (deletes branches)
#   scripts/branch-prune.sh --dry-run # report what would be deleted; do not act
#
# Discovered 2026-05-19 (after the 2026-05-18 marathon left 49+ stale
# branches). The for-loop is small enough to memorize but easy to forget;
# scripting it avoids re-deriving the incantation each time.

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ] || [ "${1:-}" = "-n" ]; then
    DRY_RUN=1
fi

ts() { date -u +%H:%M:%SZ; }

count_deleted=0
count_kept_open=0
count_kept_nopr=0
count_kept_current=0

current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")

# Iterate every local branch except main
for br in $(git branch --format='%(refname:short)' | grep -v '^main$'); do
    if [ "$br" = "$current_branch" ]; then
        count_kept_current=$((count_kept_current + 1))
        continue
    fi

    state=$(gh pr list --state all --head "$br" --json state --jq '.[0].state // "NONE"' 2>/dev/null || echo "ERROR")

    case "$state" in
        MERGED|CLOSED)
            if [ "$DRY_RUN" -eq 1 ]; then
                echo "[$(ts)] [dry-run] would delete: $br (PR state=$state)"
            else
                git branch -D "$br" >/dev/null 2>&1 || {
                    echo "[$(ts)] ERROR: failed to delete $br (may be in a worktree)"
                    continue
                }
                echo "[$(ts)] deleted: $br (PR state=$state)"
            fi
            count_deleted=$((count_deleted + 1))
            ;;
        OPEN)
            count_kept_open=$((count_kept_open + 1))
            ;;
        NONE|ERROR)
            count_kept_nopr=$((count_kept_nopr + 1))
            ;;
        *)
            echo "[$(ts)] unexpected PR state for $br: $state; keeping" >&2
            count_kept_nopr=$((count_kept_nopr + 1))
            ;;
    esac
done

mode="applied"
[ "$DRY_RUN" -eq 1 ] && mode="dry-run"

cat <<EOF

branch-prune.sh ($mode):
  ${count_deleted} branch(es) deleted
  ${count_kept_open} branch(es) kept (PR is OPEN)
  ${count_kept_nopr} branch(es) kept (no PR found or unknown state)
  ${count_kept_current} branch(es) kept (currently checked out)
EOF
