---
category: Added
---

- `infra/iam`: added an inline policy to the `panakoes-dev-gpu-instance` role granting the SQS receive/delete on pool-frame queues, DDB read/write on the streaming-sessions row, DDB read on the frame-pool table, S3 PutObject under `streaming/*` on the transcripts bucket (plus KMS Decrypt/GenerateDataKey for the bucket CMK), `execute-api:ManageConnections` on the WS API, and `cloudwatch:PutMetricData`. Without these, the transcriber-stream container running on the GPU EC2 cannot consume frames, persist state, send transcripts back over the WebSocket, or emit metrics.
