---
category: Added
---

- `services/gpu-spawner`: LRU concurrent-session eviction. The spawn callback now checks how many GPU EC2s are already running and, if at or above `max_concurrent_sessions` (default 1, configurable via `MAX_CONCURRENT_SESSIONS` env var on the task def), terminates the oldest before launching the new one. Keeps the system self-healing under the account's vCPU service quota: a forgotten tab or hung session no longer blocks new sessions until the quota is raised. The eviction emits a `session-evicted` status envelope to the new session's WebSocket so the SPA event log shows what happened. The cap should rise as the account's G/VT vCPU quota is bumped (each g4dn.xlarge eats 4 vCPU).
