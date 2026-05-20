---
category: Fixed
---

- `infra/iam`: gpu-spawner IAM policy now allows the `UserId` tag key in the `LaunchGpuInstance` `aws:TagKeys` condition. Without it, `ec2:RunInstances` is denied because the spawner stamps `UserId` on every instance for audit-trail; the `ForAllValues:StringEquals` condition fails when any unlisted tag key is present.
