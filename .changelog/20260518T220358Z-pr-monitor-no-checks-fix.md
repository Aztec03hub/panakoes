---
category: Fixed
---

- `scripts/pr-monitor.py`: add NO-CHECKS event detection for the canonical "stuck after rebase" pattern. When a PR's update-branch nudge or auto-rebase races with mid-flight CI, the new (rebased) head can land in a state where the old head's checks are dropped but no new checks fire. PR sits in mergeStateStatus=BLOCKED indefinitely because required checks do not exist. Symptom in `gh pr checks` is `no checks reported on the branch`. The monitor now emits `NO-CHECKS #<N> (open + BLOCKED + zero checks; run scripts/pr-unstick.sh <N>)` exactly once per occurrence, pointing at the canonical recovery. Discovered during PR #385's own merge cycle where the v3 monitor was silent on two PRs that had no checks at all.
