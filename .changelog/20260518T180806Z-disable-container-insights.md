---
category: Changed
---

- `infra/dev/ecs`: disable ECS Container Insights on the `panakoes-dev` cluster. Post-Wave-1 cost audit (2026-05-18) attributed ~$44/mo to the 233 paid Container Insights metrics, the single largest dev cost line. ECS task-level CPU / memory remains visible via the ECS API (which the health-aggregator dashboard already consumes). Production should re-enable this once steady-state metric volume and observability needs are known.
