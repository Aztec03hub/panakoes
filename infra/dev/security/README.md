# Dev Environment Security Services

Per-environment Terraform configuration provisioning the dev security
observability stack: AWS Config (resource-state recording + managed
rules), Amazon GuardDuty (threat detection across CloudTrail / VPC
Flow Logs / DNS), and AWS Security Hub (cross-service findings
aggregation with two compliance standards).

State lives at `dev/security/terraform.tfstate` in the shared S3
backend created by `infra/bootstrap/`.

## Plan-clean by default

Every paid component is gated behind a `bool` variable defaulting to
`false`:

| Variable              | Default | What flipping to true does                                     |
|-----------------------|---------|----------------------------------------------------------------|
| `enable_config`       | false   | Starts the Config recorder; rule evaluations begin billing     |
| `enable_guardduty`    | false   | Detector ingests CloudTrail + VPC Flow Logs + DNS              |
| `enable_security_hub` | false   | Provisions the Security Hub account + two standards            |

The supporting infrastructure (S3 bucket, KMS CMK, IAM role,
detector resource with `enable = false`) is always provisioned so the
flip is a one-line variable change rather than a from-scratch create.

This split keeps `terraform apply` safe to run unattended: the only
fixed cost a default apply incurs is the KMS CMK ($1/month) and the
empty S3 bucket (rounding error).

## What this creates

### KMS CMK `alias/panakoes-dev-security`

Single CMK encrypting the Config delivery-channel S3 bucket today and
intended for future GuardDuty / Security Hub findings exports.
Rotation enabled, 30-day deletion window. Key policy grants the AWS
Config service principal `kms:GenerateDataKey + kms:Decrypt +
kms:DescribeKey` scoped to this account via `kms:CallerAccount`.

### S3 bucket `panakoes-dev-config-<suffix>`

Delivery channel destination. Mirrors the storage-tier hardening
pattern: dedicated CMK, versioning, public access blocked, TLS-only
bucket policy. The bucket policy additionally grants AWS Config the
`s3:GetBucketAcl + s3:ListBucket + s3:PutObject` privileges its
delivery channel needs (with `AWS:SourceAccount` confused-deputy
defense).

Lifecycle is cost-disciplined for dev:

| Day | Transition |
|-----|------------|
| 90  | STANDARD -> STANDARD_IA |
| 365 | Expiry |
| 90  | Noncurrent versions expire |

Compliance retention can be extended in production.

### IAM service role `panakoes-dev-config`

Trusts `config.amazonaws.com` and carries the AWS-managed
`AWS_ConfigRole` policy. AWS keeps that policy current as new
resource types become Config-recordable, so we don't have to chase
the schema.

### AWS Config recorder + delivery channel + rules

- `aws_config_configuration_recorder.this` named `panakoes-dev-config`,
  `recording_group { all_supported = true include_global_resource_types = true }`.
- `aws_config_delivery_channel.this` writing to the bucket above with
  the AWS-default 24-hour snapshot cadence.
- `aws_config_configuration_recorder_status.this` honoring
  `var.enable_config` (default false).
- Three managed rules deployed via `for_each` over `var.config_rules`:
  - `s3-bucket-public-read-prohibited`
  - `iam-password-policy`
  - `incoming-ssh-disabled`

Adding a rule is a one-line edit to the `config_rules` set.

### Amazon GuardDuty detector

`aws_guardduty_detector.this` with `enable = var.enable_guardduty`
(default false) and 15-minute publishing frequency. Resource exists in
every plan so flipping the flag is a one-line update.

We deliberately omit S3 Protection, EKS Audit Logs, Malware Protection
for EC2, RDS Login Events, and Lambda Network Activity. The first is
deferred until ingestion volume warrants it; the rest do not apply in
dev. See `main.tf` for per-data-source pricing notes.

### AWS Security Hub (count-gated)

`aws_securityhub_account` plus two `aws_securityhub_standards_subscription`
resources, all gated by `count = var.enable_security_hub ? 1 : 0`.
Standards subscribed:

- AWS Foundational Security Best Practices v1.0.0
- CIS AWS Foundations Benchmark v1.4.0

Security Hub has no API-level enable/disable; resource presence is
the enable signal. Disabling later is a destroy-on-next-apply.

## Apply

    cd infra/dev/security
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS and random providers, then
initializes the S3 backend (the bucket created by `infra/bootstrap/`).

The first apply creates the bucket, KMS key, IAM role, recorder,
delivery channel, three Config rules, and a stopped GuardDuty
detector. Nothing starts ingesting / billing until the
`enable_*` variables flip.

## Post-apply enablement

Each service is independently flippable. Always pair the variable
flip with the corresponding `terraform apply`; the AWS console and
CLI commands shown below are equivalent ways to verify state but
should not be used to drift configuration away from the Terraform
source.

### AWS Config

    # Recommended: flip via Terraform (state stays accurate).
    AWS_PROFILE=lafayettelabs terraform apply -var enable_config=true

    # Verify the recorder is recording:
    aws configservice describe-configuration-recorder-status \
      --configuration-recorder-names panakoes-dev-config

    # The first snapshot lands in the bucket within 24 hours; force
    # an immediate snapshot for a faster signal:
    aws configservice deliver-config-snapshot \
      --delivery-channel-name panakoes-dev-config

    # List rule compliance once a few minutes have passed:
    aws configservice describe-compliance-by-config-rule

Cost note at dev volumes: ~3 rules * ~5 evaluations/day = ~$0.015/mo
in rule fees. Recorder state changes are charged per item recorded
($0.003/item); a sub-100-resource dev environment lands well under
$1/month.

### GuardDuty

    AWS_PROFILE=lafayettelabs terraform apply -var enable_guardduty=true

    # Verify:
    aws guardduty list-detectors
    aws guardduty get-detector --detector-id <id-from-listing>

    # Generate a sample finding to test wire-up downstream
    # (Slack/Email/EventBridge rule):
    aws guardduty create-sample-findings --detector-id <id>

Cost note at dev volumes: $1/M CloudTrail events + $1/GB Flow Logs +
$4/M DNS queries. Sub-dollar per month at our anticipated dev usage.

### Security Hub

    AWS_PROFILE=lafayettelabs terraform apply -var enable_security_hub=true

    # Verify:
    aws securityhub describe-hub
    aws securityhub get-enabled-standards

    # First findings populate within 1-2 hours after the first set of
    # checks evaluate. Force an evaluation cycle by re-applying or
    # waiting for the daily scheduled cycle.

Cost note at dev volumes: ~200 checks * $0.0010/check/eval ~= $0.20
per full evaluation cycle. Daily evaluations cap at ~$6/month in the
worst case; observed dev usage tends to be a fraction of that
because most checks short-circuit on missing target resources.

## Rollback

Each service can be rolled back independently. Use Terraform, not
the console, so state stays authoritative.

    # Stop AWS Config:
    AWS_PROFILE=lafayettelabs terraform apply -var enable_config=false

    # Disable GuardDuty (detector remains; ingestion stops):
    AWS_PROFILE=lafayettelabs terraform apply -var enable_guardduty=false

    # Disable Security Hub (destroys the account-level resource and
    # both standards subscriptions; their findings persist for 90
    # days per the AWS retention default and then drop):
    AWS_PROFILE=lafayettelabs terraform apply -var enable_security_hub=false

Full module destroy:

    AWS_PROFILE=lafayettelabs terraform destroy

The S3 bucket cannot be destroyed while it holds objects. If a
delivery has occurred, empty the bucket (`aws s3 rm
s3://panakoes-dev-config-<suffix>/ --recursive`) before the destroy
or set `force_destroy = true` on the bucket resource (intentionally
not set today; protects against accidental data loss).

## Consuming outputs from other configs

Future security-tier modules (CloudTrail organization trail,
findings-export pipelines, alerting rules) read this module's
outputs via a `terraform_remote_state` data source:

    data "terraform_remote_state" "security" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/security/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.security.outputs.kms_key_arn
    #   data.terraform_remote_state.security.outputs.config_bucket_arn
    #   data.terraform_remote_state.security.outputs.detector_id

## Cost expectations (default plan-clean state)

| Component                        | Monthly cost (dev)   |
|----------------------------------|----------------------|
| KMS CMK (`alias/panakoes-dev-security`) | $1 fixed       |
| S3 bucket (empty)                | < $0.01              |
| IAM role / Config rules / detector (idle) | $0          |
| **Total fixed (default apply)**  | **~$1/month**        |

Once enabled, dev-volume incremental costs are sub-dollar per
service (Config) up to a few dollars per service (Security Hub
during a heavy evaluation cycle). Real burn comes from production
volumes; budget there separately.

## Outputs

| Output                     | Type   | Purpose                                              |
|----------------------------|--------|------------------------------------------------------|
| `config_bucket_arn`        | string | ARN of the Config delivery-channel S3 bucket         |
| `config_bucket_name`       | string | Name of the Config delivery-channel S3 bucket        |
| `kms_key_arn`              | string | ARN of the security-tier CMK                         |
| `kms_key_alias`            | string | Alias name (`alias/panakoes-dev-security`)           |
| `recorder_arn`             | string | ARN of the Config recorder                           |
| `recorder_name`            | string | Name of the Config recorder                          |
| `config_role_arn`          | string | ARN of the IAM service role Config assumes           |
| `detector_id`              | string | GuardDuty detector ID                                |
| `detector_arn`             | string | GuardDuty detector ARN                               |
| `security_hub_account_arn` | string | Security Hub account ARN (empty when disabled)       |
