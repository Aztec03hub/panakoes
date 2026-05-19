---
category: Added
---

- `infra/dev/iam` + `infra/dev/ecs`: codified ECS Exec on the auth service. Adds `ssmmessages:Create{Control,Data}Channel` + `Open{Control,Data}Channel` to the auth task role and sets `enable_execute_command = true` on the auth ECS service. Required by `services/auth/scripts/seed-admin.sh` and ad-hoc debugging. Previously hot-patched live via `aws iam put-role-policy` and `aws ecs update-service --enable-execute-command`; this PR matches Terraform state to live and prevents drift on next destroy/recreate.
