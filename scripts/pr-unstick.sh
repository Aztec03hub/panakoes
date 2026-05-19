#!/usr/bin/env bash
# pr-unstick.sh: close + immediately reopen a PR to kick stuck CI.
#
# When `scripts/pr-monitor.py` emits a STALLED or NO-CHECKS event, the
# canonical fix is to close the PR and immediately reopen it. GitHub
# re-triggers all required checks on reopen.
#
# IMPORTANT: closing a PR disarms its auto-merge. Reopening does NOT
# automatically restore it. This script preserves the auto-merge state:
# it captures whether auto-merge was armed before close, then re-arms
# after reopen. Discovered the hard way 2026-05-18 (PRs #369 + #370 sat
# CLEAN for 4 min with no auto-merge to fire after the v1 unstick).
#
# Usage:
#   scripts/pr-unstick.sh <PR_NUMBER> [--repo owner/repo]
#
# Idempotent: re-running on an already-open PR closes+reopens again.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <PR_NUMBER> [--repo owner/repo]"
  exit 1
fi

PR="$1"
REPO_ARG=()
if [ "${2:-}" = "--repo" ] && [ -n "${3:-}" ]; then
  REPO_ARG=(--repo "$3")
fi

ts() { date -u +%H:%M:%SZ; }

# Capture auto-merge state BEFORE close (close clears autoMergeRequest)
AUTO_MERGE_ARMED=$(gh pr view "$PR" "${REPO_ARG[@]}" --json autoMergeRequest \
  --jq '.autoMergeRequest // "" | if . == "" then "no" else "yes" end')

echo "[$(ts)] Unsticking PR #$PR (auto-merge was: $AUTO_MERGE_ARMED). Close + reopen to retrigger CI."
gh pr close "$PR" "${REPO_ARG[@]}" 2>&1 | tail -2
sleep 2
gh pr reopen "$PR" "${REPO_ARG[@]}" 2>&1 | tail -2

if [ "$AUTO_MERGE_ARMED" = "yes" ]; then
  # Wait a moment for GitHub to settle the reopen before re-arming
  sleep 2
  echo "[$(ts)] Re-arming auto-merge (was armed before close)"
  # NOTE: omit --delete-branch because the branch may be in an active
  # worktree; gh treats the local-delete-failure as the whole command's
  # exit code, which would falsely flag the re-arm as failed.
  gh pr merge "$PR" "${REPO_ARG[@]}" --auto --squash 2>&1 | tail -2

  # v3 (2026-05-19): after re-arming, check whether the PR is BEHIND main.
  # Auto-merge does NOT automatically run update-branch for BEHIND PRs unless
  # the repo has "Allow auto-merge" + "Always suggest updating branches"
  # configured AND the branch protection requires up-to-date branches. Even
  # then, auto-merge can sit dormant. Manual nudge via the GH REST API
  # update-branch endpoint kicks GitHub to merge main into the PR branch,
  # which retriggers CI and lets auto-merge fire on completion. See memory
  # `pr-unstick-v3-needed-for-behind-after-unstick.md`.
  sleep 3
  MERGE_STATE=$(gh pr view "$PR" "${REPO_ARG[@]}" \
    --json mergeStateStatus --jq '.mergeStateStatus')
  echo "[$(ts)] Post-rearm mergeStateStatus: $MERGE_STATE"

  if [ "$MERGE_STATE" = "BEHIND" ]; then
    # Derive owner/repo for the REST API call. If --repo was passed, use it;
    # else fall back to the current repo's nameWithOwner.
    if [ ${#REPO_ARG[@]} -eq 2 ]; then
      NWO="${REPO_ARG[1]}"
    else
      NWO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
    fi
    echo "[$(ts)] PR is BEHIND main; nudging update-branch via REST API ($NWO)"
    if gh api -X PUT "repos/$NWO/pulls/$PR/update-branch" 2>&1 | tail -2; then
      echo "[$(ts)] update-branch nudged; GitHub will merge main into the PR branch and retrigger CI"
    else
      echo "[$(ts)] update-branch nudge failed (see gh output above). PR may need manual rebase."
    fi
  fi
fi

echo "[$(ts)] Done. Watch the pr-monitor stream for CI re-firing."
