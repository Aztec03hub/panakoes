# ADR-034: CloudFront Standard Logs via CloudWatch Logs Delivery (v2)

## Status

Accepted. Implemented in `infra/dev/frontend` (PR #160, applied 2026-05-09).

## Context

The `infra/dev/frontend` Terraform module provisions a CloudFront distribution for the SvelteKit admin app, plus a separate S3 bucket to receive CloudFront access logs. The first apply on 2026-05-09 failed at distribution-creation time with:

```
InvalidArgument: The S3 bucket that you specified for CloudFront logs does not enable ACL access.
```

The legacy CloudFront standard logs (v1) path requires the destination S3 bucket to grant the `awslogsdelivery` canonical user `WRITE` permission via an S3 ACL with the `log-delivery-write` canned ACL grant. AWS, since approximately 2023, defaults new S3 buckets to `BucketOwnerEnforced` ObjectOwnership, which **disables ACLs entirely** on the bucket. The two settings are mutually exclusive: the bucket either supports ACLs (older default `ObjectWriter`) or it does not (current default `BucketOwnerEnforced`). CloudFront v1 logging requires the former; AWS's secure default is the latter.

Workarounds for v1:

1. **Set `aws_s3_bucket_ownership_controls.frontend_logs.rule.object_ownership = "ObjectWriter"`** to re-enable ACLs, then add `aws_s3_bucket_acl.frontend_logs` granting the `log-delivery-write` ACL. This works mechanically but reverts the bucket to a less-secure ACL-based access model that AWS itself recommends against. It also creates a TF state divergence: the `aws_s3_bucket_ownership_controls` resource and the `aws_s3_bucket_acl` resource have to be applied in a specific order, and certain AWS regions still reject the combination.

2. **Disable CloudFront access logging entirely.** Phil explicitly rejected this for dev: access logs are operationally valuable (debugging cache-miss patterns, rate-limit tuning, geo distribution), and dev is the environment where you discover the value before production traffic hits.

A third option exists: CloudFront Standard Logs **v2**, released 2024 ([AWS announcement](https://aws.amazon.com/blogs/aws/cloudfront-standard-logs-v2-with-additional-fields-and-cloudwatch-logs-delivery)). v2 routes access logs through the CloudWatch Logs Delivery service (the same vended-logs path used by WAF, VPC flow logs, AWS Network Firewall, and others), which writes to S3 on CloudFront's behalf using the `delivery.logs.amazonaws.com` service principal authorized by the bucket policy. The bucket stays on `BucketOwnerEnforced`. ACLs stay disabled. The legacy permissions model is bypassed entirely.

## Decision

`infra/dev/frontend` uses CloudFront Standard Logs v2 (CloudWatch Logs Delivery to S3), not the legacy v1 (S3 + ACL) path. This is the standing convention for any future CloudFront distribution Panakoes provisions: v2 is mandatory; v1 is forbidden.

The Terraform shape for v2:

1. **Bucket policy** grants `delivery.logs.amazonaws.com` two actions:
   - `s3:PutObject` (writes log files), conditioned on `s3:x-amz-acl = bucket-owner-full-control`, `aws:SourceAccount = <our account>`, and `aws:SourceArn` matching `arn:aws:logs:<region>:<account>:delivery-source:*` (least-privilege: only delivery sources owned by this account can write).
   - `s3:GetBucketAcl` (CWL Delivery preflight check on the destination), conditioned on `aws:SourceAccount`.

2. **`aws_cloudwatch_log_delivery_source.frontend_access`** registers the CloudFront distribution as a producer of `ACCESS_LOGS`-type events.

3. **`aws_cloudwatch_log_delivery_destination.frontend_access_s3`** declares the S3 bucket as the sink with `output_format = "json"`.

4. **`aws_cloudwatch_log_delivery.frontend_access`** wires source to destination, with a `suffix_path = "cloudfront/{yyyy}/{MM}/{dd}/{HH}/"` for time-partitioned objects (efficient Athena `PARTITION BY` later).

5. The `aws_cloudfront_distribution.admin` block has **no** `logging_config { ... }` (that block is the v1 trigger; its absence routes logs through v2's external delivery wiring).

## Consequences

**Positive:**

- Bucket stays on `BucketOwnerEnforced` (AWS-recommended default; ACLs disabled). Aligns with Trusted Advisor / Security Hub best-practice rules out of the box.
- Distribution apply succeeds against the modern S3 default with no `aws_s3_bucket_ownership_controls` workaround. One fewer resource in state.
- Logs are time-partitioned at write time, so future Athena queries can prune by partition without scanning the whole bucket.
- Pattern is uniform with WAF logging (`aws_cloudwatch_log_delivery_*` resources are the same shape across producers). When we add VPC Flow Logs or NLB access logs in production, the operator already knows the model.
- v2 supports additional fields (TLS handshake details, edge-location codes, request IDs) that v1 lacks. Not yet enabled, but the path is unblocked.

**Negative:**

- Three additional Terraform resources per distribution (delivery source, destination, delivery wiring) versus a single inline `logging_config` block. The verbosity is the cost of decoupling the producer from the destination.
- CloudWatch Logs Delivery has its own quotas (delivery destinations per account, suffix path length). At our scale these are not a concern; would matter at hundreds of distributions.
- One indirection: the bucket policy authorizes a service principal that isn't visible in any single resource. New operators have to read both the policy and the delivery resources to understand the auth model. Mitigated by inline comments in `infra/dev/frontend/main.tf`.

**Operator-side impact:**

Bucket-level grants no longer use the `log-delivery-write` canned ACL. Any future module that copies the v1 pattern from a stale tutorial will hit the same `InvalidArgument` failure we did. This ADR is the canonical reference; readers spotting `aws_s3_bucket_acl` + `log-delivery-write` in any future PR should reject it and link to this ADR.

## References

- PR #160, the implementation that switched `infra/dev/frontend` from v1 to v2.
- `infra/dev/frontend/main.tf`, the canonical example. Inline comments mark the bucket-policy statements and the three delivery resources.
- AWS docs: [Configure CloudFront standard logs (v2)](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/standard-logging.html).
- AWS announcement (2024): [CloudFront Standard Logs v2](https://aws.amazon.com/blogs/aws/cloudfront-standard-logs-v2-with-additional-fields-and-cloudwatch-logs-delivery/).
- `feedback_panakoes_lessons.md` memory entry; this ADR is the project-side codification.
