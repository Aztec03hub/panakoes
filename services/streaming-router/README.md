# services/streaming-router

API Gateway v2 WebSocket router Lambda for the panakoes streaming pipeline.
Every WebSocket frame on the `panakoes-dev-streaming-ws` API targets this
Lambda. Branching on `event.requestContext.routeKey`:

| Route | Behavior |
| --- | --- |
| `$connect` | Write a session row to `panakoes-dev-streaming-sessions` with `status=connecting`, persist `connection_id` + `user_id` from the authorizer context. Emit a `streaming.session.connecting` event to EventBridge so the gpu-spawner reacts. |
| `$disconnect` | Update the session row: `status=disconnected`, `disconnected_at=now()`. |
| `audio-frame` | Forward the frame body to the per-session SQS queue (`panakoes-dev-streaming-ws-frames`). The GPU worker reads from this queue. |
| `transcript-request` | Return the last-known transcript stored on the session row (stub for now; the GPU worker is responsible for keeping this fresh). |
| `$default` | Log the unknown action at WARN, return 200. Forward-compat. |

## Authorizer context contract

Every $connect event carries the authorizer's emitted context at
`event.requestContext.authorizer.lambda` (REQUEST-type Lambda authorizer
attached to the WebSocket `$connect` route). The router reads `user_id`,
`tenant_id`, and `role` from that map; any missing field is treated as the
empty string for persistence but never as a permission grant.

## Environment

| Variable | Default | Notes |
| --- | --- | --- |
| `STREAMING_SESSIONS_TABLE` | (required) | DynamoDB table name |
| `AUDIO_FRAME_QUEUE_URL` | (required) | SQS queue URL the GPU worker drains |
| `STREAMING_EVENT_BUS` | `default` | EventBridge bus the gpu-spawner subscribes to |
| `AWS_REGION` | `us-east-1` | |
