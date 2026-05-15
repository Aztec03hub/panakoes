---
category: Fixed
---

- `infra/dev/ecs`: added HTTPS internet egress (port 443 → 0.0.0.0/0) to all 11 ECS task security groups; required for tasks on public subnets to reach Secrets Manager, ECR auth, and CloudWatch Logs without VPC interface endpoints.
- `infra/dev/ecs`: removed redundant NLB load_balancer blocks from auth, admin-api, cost-api, and health-aggregator ECS services (those services now register only with the shared ALB target groups).
