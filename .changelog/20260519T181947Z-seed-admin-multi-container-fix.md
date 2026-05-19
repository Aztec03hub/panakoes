---
category: Fixed
---

- `services/auth/scripts/seed-admin.sh`: now passes `--container auth` to `aws ecs execute-command`, fixing the `InvalidParameterException: For tasks containing multiple containers, you must specify a container name` error introduced when ECS Service Connect added a sidecar container to the auth task. Override via `CONTAINER=...` if the application container is ever renamed.
