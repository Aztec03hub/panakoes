---
category: Added
---

- `scripts/pr-monitor.py` + `scripts/pr-unstick.sh`: orchestrator-side observability for PR + CI state. The monitor script (Python, designed for Claude Code's Monitor tool, persistent mode) polls `gh pr list` every 30s and emits one event line per meaningful state change: MERGED / CLOSED / BEHIND / DIRTY / CLEAN / UNSTABLE / CI-FAIL / CI-RECOVERED / STALLED (a pending check held for >15min, the canonical hung-CI signature). Heartbeat lines every ~5min prove the monitor is alive even when state is steady; an `/tmp/pr-monitor-live.lastpoll` sidecar timestamp lets the orchestrator verify liveness on demand. `pr-unstick.sh <PR>` closes + reopens a stuck PR to retrigger CI (the canonical recovery for STALLED checks). `WORKFLOW.md` section 5.5 documents the dispatch pattern and ties off the "magical thinking" failure mode where auto-merge fires silently and Claude has no feedback channel.
