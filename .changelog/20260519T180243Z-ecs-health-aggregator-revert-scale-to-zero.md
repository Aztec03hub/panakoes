---
category: Fixed
---

- `infra/dev/ecs`: `health_aggregator_desired_count` reverted to 1 (was 0 as of PR #423). Surfaced once admin.panakoes.com went live: the SPA's first dashboard fetch hits `/v1/health-aggregator/health` on page load, and the scaled-to-zero service returned 503 from the ALB. Summarization (also scaled in PR #423) stays at 0; it's event-driven from SQS and the SPA does not call it directly.
