---
category: Changed
---

- `infra/dev/api-gateway`: replaced 11 per-NLB VPC Link integrations with a single shared ALB integration; routing now uses `X-Panakoes-Service` header instead of path-based NLB discovery. Added routes for `ingestion-api`, `query-api`, `session-manager`, `billing`.
- `infra/dev/alb`: listener rules updated from path-pattern to http_header (`x-panakoes-service`) for 8 public services; removed obsolete rules for 3 internal-only services (summarization, notification, gpu-spawner).
