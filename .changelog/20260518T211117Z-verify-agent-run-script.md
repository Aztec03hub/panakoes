---
category: Added
---

- `scripts/verify-agent-run.sh`: mechanical post-completion verification for sub-agent dispatches; runs 8 checks (run-report frontmatter, status, file-list match against `git diff`, em-dash scan, gitleaks, progress-log clean termination, Conventional Commits, Local-First Verification section presence) with one exit code per check class.
- `WORKFLOW.md`: pinned the self-assessment ritual cadence (every 3 dispatches or every milestone, always at session-end, immediately after a 2-3-strike friction recurrence or a discovered discipline gap) and added a failure-mode entry for skipping self-assessment after friction recurs.
