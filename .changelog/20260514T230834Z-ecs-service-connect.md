---
category: Changed
---

- `infra/dev/ecs`: added ECS Service Connect to all 11 services (namespace: `panakoes-dev.local`); registered all 8 public services with shared ALB target groups; added ALB SG ingress rules for 8 public services (auth, admin-api, billing, cost-api, health-aggregator, ingestion-api, query-api, session-manager); removed unused VPC Link ingress rules from 3 internal-only services (summarization, notification, gpu-spawner); updated portMapping names on all task definitions to satisfy Service Connect requirements.
