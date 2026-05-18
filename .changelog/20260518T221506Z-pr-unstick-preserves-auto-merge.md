---
category: Fixed
---

- `scripts/pr-unstick.sh`: preserve auto-merge state across close + reopen. `gh pr close` disarms auto-merge; `gh pr reopen` does NOT restore it. The v1 unstick (shipped in PR #385) silently left PRs with cleared auto-merge after the close+reopen cycle, so PRs that reached CLEAN sat indefinitely with no merge actor. The fix: query `autoMergeRequest` before close, capture whether it was armed, and re-arm after reopen if so. Also drops `--delete-branch` from the re-arm because the branch is often in an active worktree and gh treats local-delete-failure as the entire command's exit code (would falsely flag the re-arm as failed). Discovered the same session v1 shipped: PRs #369 + #370 sat CLEAN for 4 minutes after unstick before the orchestrator noticed the auto-merge had cleared.
