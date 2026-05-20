# streaming-frame-queues

Owns the pre-allocated SQS frame-queue pool plus the DynamoDB pool-state table backing the gpu-spawner's drain-then-claim protocol for real-time streaming transcription sessions.

## What this module creates

- 32 standard SQS queues named `panakoes-dev-stream-frames-pool-{0..31}`.
  - 30-second visibility timeout (frames are short-lived; redeliver fast on consumer death).
  - 1-hour message retention (far longer than any live session needs; the drain-then-claim covers residual messages anyway).
  - Encrypted with the consolidated `panakoes/app-data` CMK from `infra/dev/kms/`.
- One DynamoDB table named `panakoes-dev-stream-frame-pool` keyed on `pool_queue_id` (number).
  - One row per queue, seeded with the row's `queue_url`.
  - `claimed_by` (string) + `claimed_at` (string) are written by gpu-spawner during a session's lifetime and removed by the lifecycle reaper.

## Why pre-allocated pool

`CreateQueue` and `DeleteQueue` are heavyweight control-plane calls (~30 TPS account ceiling, 60-second tombstone on name reuse) and `PurgeQueue` has a documented window during which messages sent post-purge may be silently deleted. None of those latencies are acceptable for per-session use; the design doc's "Frame-queue strategy (CRIT-01 + HIGH-06 fix)" section covers the rationale in depth.

Pool size of 32 provides ~3x headroom over the cost model's peak target of 10 concurrent sessions. Raise `var.pool_size` for higher concurrency.

## Consumers

- **gpu-spawner** reads + writes the DDB pool-state table to claim/release slots, polls each queue when assigned. IAM additions in `infra/dev/iam/main.tf`.
- **streaming-router** sends frames into the per-session queue via the URL stored on the session row at claim time. The queue URL is opaque to the router; it does not hardcode the pool.

## Bootstrap order

1. Apply `infra/dev/kms/` (provides `app_data_key_arn`).
2. Apply this module.
3. Apply `infra/dev/iam/` to grant the gpu-spawner the SQS + DDB perms on the freshly-created ARNs.
4. Apply `infra/dev/ecs/` (gpu-spawner task gets the new env vars).
