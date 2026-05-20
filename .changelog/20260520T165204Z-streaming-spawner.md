---
category: Added
---

- `services/gpu-spawner`: EventBridge ➜ SQS spawn-queue consumer (`eventbridge_consumer.py`) so the spawner auto-fires on `streaming.session.connecting` without an HTTP roundtrip; plus the drain-then-claim frame-queue pool client (`pool_claim.py`) and a structured RunInstances error taxonomy in `aws/ec2.py`.
- `services/streaming-router`: ping / ping-echo keepalive routes, queryStringParameters reads for `parent_session_id` + `prompt_seed_text`, per-Lambda warm cache of `connection_id` -> `frame_queue_url` with FIFO eviction + `$disconnect` invalidation, INFO-level cold-start drop, and a 2-hour `ttl_epoch_seconds` default on every `$connect` row.
- `infra/dev/api-gateway-ws`: ping + ping-echo registered on `local.app_routes` so API GW dispatches them to the router (BLOCK-01 round-4 fix).
- `infra/dev/streaming-frame-queues`: new module owning the 32-slot pre-allocated SQS frame pool and the `panakoes-dev-stream-frame-pool` DDB pool-state table, both seeded at apply time.
- `infra/dev/events`: new `panakoes-dev-spawn-queue` + DLQ + EventBridge rule routing `streaming.session.connecting` from the Panakoes bus to the queue, plus a DLQ-not-empty CloudWatch alarm.
- `infra/dev/iam`: gpu-spawner gains `sqs:ReceiveMessage` on the spawn queue, `dynamodb:Scan/UpdateItem/GetItem` on the frame-pool table, drain perms on the pool queues, and a `panakoes/streaming` PutMetricData grant; transcriber-stream gains `execute-api:ManageConnections` and SQS consume on the pool, and its custom-metric namespace expands to cover `panakoes/streaming`.
- `infra/dev/data`: streaming-sessions TTL attribute renamed from `expires_at` to `ttl_epoch_seconds` to match the router's writer.
- `infra/dev/ecs/gpu_spawner.tf`: new env vars `STREAMING_GPU_AMI_ID`, `SPAWN_QUEUE_URL`, `STREAM_FRAME_POOL_TABLE`, `STREAMING_SESSIONS_TABLE`; `streaming_gpu_ami_id` placeholder variable added in `variables.tf`.
- `infra/dev/observability/dashboards/streaming.json`: new CloudWatch dashboard wired from `main.tf` covering frame routing, spawn outcomes, GPU/jitter latency, transcript emission, drain triggers, and spawn-queue depth.
