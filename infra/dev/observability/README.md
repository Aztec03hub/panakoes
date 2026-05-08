# Dev Environment Observability

Per-environment Terraform configuration creating the CloudWatch
observability primitives for the Panakoes `dev` environment: a
dedicated KMS CMK, one log group per service, a long-term S3 log
archive, an IAM role for CloudWatch Logs to ship into the archive, and
per-service metric filters tracking error counts. State lives at
`dev/observability/terraform.tfstate` in the shared S3 backend created
by `infra/bootstrap/`.

## What this creates

### KMS CMK `alias/panakoes-dev-logs`

Dedicated customer-managed KMS key encrypting both the CloudWatch log
groups and the S3 archive bucket. Single-key scoping keeps blast
radius small (a leaked log-consumer credential cannot also touch the
audio-uploads or transcripts tier) and keeps Athena query setup
simple (one decrypt grant covers the entire log pipeline).

- Rotation enabled.
- Deletion window: 7 days. Shorter than the 30-day default used
  elsewhere because this is a dev environment we expect to rebuild;
  production observability would flip to 30 days.
- Key policy grants the regional CloudWatch Logs service principal
  `kms:Encrypt + kms:Decrypt + kms:GenerateDataKey* + kms:Describe*`,
  scoped via the `kms:EncryptionContext:aws:logs:arn` condition to log
  groups under `/panakoes/dev/*`.

### CloudWatch Log Groups (11 services)

One log group per service at `/panakoes/dev/<service>`:

    auth, ingestion-api, summarization, notification, query-api,
    session-manager, gpu-spawner, transcriber-batch,
    transcriber-stream, event-router, billing

Each group has 30-day retention (matches the locked decision in
CLAUDE.md: hot logs in CloudWatch for 30 days, cold logs in the S3
archive thereafter) and is KMS-encrypted with the logs CMK above.

### Metric filters per service

A `aws_cloudwatch_log_metric_filter` on each log group increments
`panakoes/dev.<service>.errors_total` (custom metric) whenever an
event matches the filter pattern `?ERROR ?error_code` (substring OR).
Default value 0 means the metric reports steady state instead of
sparse data points, which makes alarms easier to define later.

### S3 log archive bucket `panakoes-dev-log-archive-<suffix>`

Mirrors the hardening pattern from `infra/dev/storage/log-archive`:
versioning, public access fully blocked, TLS-only bucket policy,
KMS-encrypted with the logs CMK. Lifecycle differs and reflects this
module's brief:

| Day | Transition |
|---|---|
| 30 | STANDARD -> STANDARD_IA |
| 90 | STANDARD_IA -> GLACIER_IR |
| 365 | GLACIER_IR -> DEEP_ARCHIVE |

A second lifecycle rule aborts incomplete multipart uploads at 7 days
to keep orphaned upload parts from accruing storage charges.

Note: this bucket is separate from the
`panakoes-dev-log-archive-<suffix>` bucket created by
`infra/dev/storage/`. The two share a name prefix but live under
different random suffixes; the one in `infra/dev/storage/` is the
historical Athena-queryable archive (7-year retention) used by the
broader application data tier, while this one is the dev observability
pipeline's own archive (no hard expiry; long-tail tiering only). A
follow-up PR may consolidate the two; doing so up front would couple
this module to `dev/storage/` more tightly than warranted at this
stage.

### IAM role `panakoes-dev-log-archiver`

Trust policy: `logs.us-east-1.amazonaws.com` (the regional CloudWatch
Logs service principal). Inline policy grants:

- `s3:PutObject` on the archive bucket's object namespace.
- `s3:GetBucketAcl` on the archive bucket itself (required by
  CloudWatch Logs S3 export to verify the destination).
- `kms:GenerateDataKey + kms:Decrypt` on the logs CMK.

The role exists in this PR even though no resource consumes it yet
(see deferral note below). Provisioning it here lets the IAM review
happen in the same PR as the bucket and key, instead of bolting it on
during the wiring PR.

## Deferred: subscription filter wiring

The brief sketched a Kinesis Data Firehose delivery stream
(`panakoes-dev-log-archive-firehose`) with one
`aws_cloudwatch_log_subscription_filter` per log group, all writing
into the archive bucket. We deliberately deferred the wiring to a
follow-up PR for two reasons:

1. **Blast-radius isolation.** Firehose adds non-trivial moving parts
   (delivery stream, dedicated IAM role, buffering tuning, error
   destination, CloudWatch error metrics). Landing it in the same PR
   as the log groups blurs the diff and makes rollback harder.
2. **Volume calibration.** The optimal Firehose buffer size and
   interval depend on the log volume across services, which we will
   only know once the services start emitting. Wiring at zero volume
   means choosing default values that we know we will retune.

The deferred work is captured as a `# TODO:` comment block at the
bottom of `main.tf`. When the wiring PR lands, it should:

- Add `aws_kinesis_firehose_delivery_stream.log_archive_firehose` with
  S3 destination on `aws_s3_bucket.log_archive`.
- Add an `aws_cloudwatch_log_subscription_filter` per service log
  group, all targeting the Firehose ARN.
- Decide whether the Firehose role reuses
  `aws_iam_role.log_archiver` or splits into a dedicated role for
  blast-radius isolation. Default to a dedicated role unless the
  policy bundles end up identical.

## Apply

    cd infra/dev/observability
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS and random providers, then
initializes the S3 backend (the bucket created by `infra/bootstrap/`).

## Consuming outputs from other configs

Service Terraform configurations (Lambda functions, ECS task
definitions, EC2 user-data scripts) read the log group ARN they should
write to via a `terraform_remote_state` data source pointing at this
config's state:

    data "terraform_remote_state" "observability" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/observability/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.observability.outputs.log_group_arns["auth"]
    #   data.terraform_remote_state.observability.outputs.kms_key_arn
    #   data.terraform_remote_state.observability.outputs.archive_bucket_name

## Cost expectations

- 11 log groups, KMS-encrypted, 30-day retention. CloudWatch Logs
  ingestion is $0.50/GB and storage is $0.03/GB-month; at dev volumes
  (assume single-digit MB per service per day) the total monthly cost
  is well under a dollar.
- Custom metrics: $0.30 per metric per month. 11 metrics is $3.30/mo
  fixed.
- KMS CMK: $1/month. Bucket-key enabled on the archive SSE config
  amortizes per-request KMS charges to bucket-level so request volume
  stays bounded.
- S3 archive: pennies for dev volumes, dropping further as objects
  tier into IA / Glacier_IR / Deep Archive.

The dominant fixed cost of this module is the 11 custom metrics
($3.30/mo) plus the CMK ($1/mo). Everything else scales with usage and
is rounding error at dev volumes.

## Outputs

| Output                  | Type    | Purpose                                                   |
|-------------------------|---------|-----------------------------------------------------------|
| `log_group_arns`        | map     | Service name -> log group ARN (for IAM grant policies)    |
| `log_group_names`       | map     | Service name -> log group name (for AWS APIs)             |
| `kms_key_arn`           | string  | ARN of the logs CMK                                       |
| `archive_bucket_name`   | string  | Name of the long-term S3 log archive bucket               |
| `archive_bucket_arn`    | string  | ARN of the log archive bucket                             |
| `log_archiver_role_arn` | string  | ARN of the IAM role CloudWatch Logs uses to ship to S3    |
