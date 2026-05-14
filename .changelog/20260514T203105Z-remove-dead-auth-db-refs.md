---
category: Fixed
---

- `infra/dev/backup`: remove dead `terraform_remote_state.auth_db` data block and `protected_cluster_arns` local now that the Aurora auth-db cluster is decommissioned; `protected_resource_arns` simplifies to `local.protected_table_arns`.
- `infra/dev/ecs`: remove dead `terraform_remote_state.auth_db` data block (no outputs were consumed; auth service uses auth-db-rds).
