---
category: Fixed
---

- `infra/iam`: the `panakoes-dev-gpu-spawner-task` role now has `execute-api:ManageConnections` on the streaming-ws API. The Stage-4 observability PR added a `StatusPublisher` in the spawner that posts events back to the SPA's WS connection (spawn-message-received, pool-claimed, session-row-updated, run-instances-issued, instance-launching, spawn-failed); without this grant every emit failed with `AccessDeniedException` and Phil saw nothing during the 5-7 min spawn window even with the rest of the obs pipeline working.
