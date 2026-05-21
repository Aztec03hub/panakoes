---
category: Fixed
---

- `services/gpu-spawner`: UserData's `post_status` bash helper now passes `--cli-binary-format raw-in-base64-out` to `aws apigatewaymanagementapi post-to-connection`. AWS CLI v2 defaults the `--data` parameter to base64-encoded bytes (or `fileb://` paths); the raw-JSON form fails with `Invalid base64` and the entire EC2 phase of the observability pipeline went silent. Discovered while validating PR #480 end-to-end.
