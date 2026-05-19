---
category: Added
---

- `infra/dev/auth-db-rds`: snapshot + restore RDS auth-db onto the consolidated `panakoes/app-data` CMK (W2-T5 deferred-task completion). Adds `aws_db_snapshot.pre_migration`, `aws_db_snapshot_copy.re_encrypted`, and `aws_db_instance.auth_db_v2`; the original `aws_db_instance.auth_db` is intentionally unchanged to avoid the ForceNew `kms_key_id` flip that would have destroyed the live user / session tables. Plan: 3 add / 0 change / 0 destroy on the original instance. Cutover (DSN flip + ECS roll) and v1 retirement land in follow-up PRs.
