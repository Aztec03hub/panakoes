---
category: Changed
---

- `infra/dev/network`, `infra/dev/iam`, `infra/dev/api-gateway`, `infra/dev/transcribe-worker`, `infra/dev/batch`: codify five live-AWS hot-patches applied during the 2026-05-20 Whisper-on-Batch sprint so `terraform plan` is a no-op against the running infrastructure. Public subnets now set `map_public_ip_on_launch = true`, the `panakoes-dev-gpu-instance` role has `AmazonEC2ContainerServiceforEC2Role` attached for ECS agent registration, the `panakoes-dev-transcriber-batch-task` trust policy includes both `ecs-tasks.amazonaws.com` and `lambda.amazonaws.com`, the dev HTTP API CORS allow-headers list adds `traceparent` and `tracestate` for W3C trace context, and the transcribe-worker Lambda's `batch:SubmitJob` IAM policy includes the bare job-definition family ARN alongside the `:*` revision wildcard. The batch module's GPU AMI default is bumped to `ami-0b729f3f75a1074c4` (ECS-Optimized GPU) and the compute environment moves to public subnets.
