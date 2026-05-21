---
category: Fixed
---

- `services/gpu-spawner`: UserData env file now writes both `AWS_REGION` and `AWS_DEFAULT_REGION` for the transcriber-stream container. boto3 in the container's `LifecycleWatcher` was calling `boto3.resource("dynamodb")` without an explicit region and was raising `NoRegionError` because the bundled botocore version doesn't honor `AWS_REGION` alone. The lifecycle task crash was killing the container right after it emitted its first `ready` message.
