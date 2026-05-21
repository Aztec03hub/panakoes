---
category: Fixed
---

- `services/gpu-spawner`: GPU EC2 launches now set `MetadataOptions.HttpPutResponseHopLimit=2` so the transcriber-stream container (running on the default Docker bridge network) can fetch instance-role credentials from IMDSv2. Without this, every boto3 call inside the container (`PostToConnection`, SQS receive, DDB read/write, S3 put) silently failed and the container never emitted its `ready` message.
