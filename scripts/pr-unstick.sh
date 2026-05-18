#!/usr/bin/env bash
# pr-unstick.sh: close + immediately reopen a PR to kick stuck CI.
#
# When `scripts/pr-monitor.py` emits a STALLED event, the canonical fix is
# usually to close the PR and immediately reopen it. GitHub re-triggers all
# required checks on reopen.
#
# Usage:
#   scripts/pr-unstick.sh <PR_NUMBER> [--repo owner/repo]
#
# Idempotent: re-running on an already-open PR does the same thing.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <PR_NUMBER> [--repo owner/repo]"
  exit 1
fi

PR="$1"
REPO_ARG=""
if [ "${2:-}" = "--repo" ] && [ -n "${3:-}" ]; then
  REPO_ARG="--repo $3"
fi

echo "[$(date -u +%H:%M:%SZ)] Unsticking PR #$PR: close + reopen to retrigger CI"
gh pr close $PR $REPO_ARG 2>&1 | tail -2
sleep 2
gh pr reopen $PR $REPO_ARG 2>&1 | tail -2
echo "[$(date -u +%H:%M:%SZ)] Done. Watch the pr-monitor stream for CI re-firing."
