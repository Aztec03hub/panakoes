locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "security"
  }

  name_prefix = "${var.project_name}-${var.environment}"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

# ---------------------------------------------------------------------------
# Random suffix
#
# AWS Config delivery-channel S3 bucket gets its own random suffix so a
# destroy + recreate cycle does not collide with the global S3 namespace
# or with any past bucket sharing the prefix. 4-byte hex matches the
# pattern used by `infra/dev/storage/`.
# ---------------------------------------------------------------------------
resource "random_id" "config_bucket_suffix" {
  byte_length = 4
}

# ===========================================================================
# KMS CMK for the dev security observability stack
#
# Single CMK encrypts the Config delivery-channel S3 bucket and is
# available for any future security-tier encryption (Security Hub
# findings export, GuardDuty findings export, CloudTrail organization
# trail). One key keeps blast-radius management simple at this tier
# while still scoping access independently of the storage / logs CMKs.
#
# Rotation enabled. Deletion window 30 days (matches storage CMKs in
# sibling modules; security artefacts are not as cheap to recreate as
# secret material so we keep the longer undelete buffer).
#
# The key policy grants the AWS Config service principal sufficient
# encrypt + decrypt access on the delivery-channel bucket via standard
# AWS Config service-principal patterns.
# ===========================================================================

resource "aws_kms_key" "security" {
  description             = "KMS CMK for ${local.name_prefix} security observability (Config delivery channel + future GuardDuty/Security Hub exports)"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = data.aws_iam_policy_document.security_kms_policy.json

  tags = local.common_tags
}

resource "aws_kms_alias" "security" {
  name          = "alias/${local.name_prefix}-security"
  target_key_id = aws_kms_key.security.key_id
}

data "aws_iam_policy_document" "security_kms_policy" {
  # Account root retains admin so IAM principals with sufficient
  # privilege can manage the key. Standard CMK policy idiom.
  statement {
    sid    = "EnableRootAccountAdmin"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions = ["kms:*"]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.security`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  # AWS Config writes encrypted snapshots and history files into the
  # delivery channel bucket; granting GenerateDataKey + Decrypt to the
  # service principal lets bucket-key amortization work without the
  # Config role needing key-level grants of its own. The
  # kms:CallerAccount condition restricts use to this account.
  statement {
    sid    = "AllowAWSConfigServiceUse"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
      "kms:DescribeKey",
    ]

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; the kms:CallerAccount
    # condition further restricts service-principal use to this account.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# ===========================================================================
# AWS Config delivery-channel S3 bucket
#
# Mirrors the hardening pattern from `infra/dev/storage/`: dedicated
# CMK, versioning, public access fully blocked, TLS-only bucket policy.
# Lifecycle is cost-disciplined for dev: STANDARD for 90 days, IA
# thereafter, hard expiry at 365 days. Compliance retention can be
# extended in production; for dev we want a tight ceiling on storage
# cost since Config writes a snapshot every 6 hours by default.
#
# The bucket policy also grants AWS Config the GetBucketAcl + PutObject
# privileges its delivery channel needs. Without these, Config writes
# fail silently and findings stop arriving.
# ===========================================================================

resource "aws_s3_bucket" "config" {
  bucket = "${local.name_prefix}-config-${random_id.config_bucket_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "config" {
  bucket = aws_s3_bucket.config.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "config" {
  bucket = aws_s3_bucket.config.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.security.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "config" {
  bucket = aws_s3_bucket.config.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "config_bucket" {
  # Deny anything not over TLS. Defense-in-depth alongside the bucket
  # ACL block above.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.config.arn,
      "${aws_s3_bucket.config.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # AWS Config requires GetBucketAcl on the bucket itself so it can
  # verify ownership before writing the first snapshot.
  statement {
    sid    = "AWSConfigBucketPermissionsCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.config.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  # AWS Config requires bucket existence to be advertisable to itself
  # at the API level. Without this statement, Config falls back to its
  # internal listing path which often fails behind tight bucket
  # policies.
  statement {
    sid    = "AWSConfigBucketExistenceCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.config.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  # PutObject under the AWSLogs/<account>/Config/ prefix is the actual
  # delivery write. The bucket-owner-full-control ACL is required by
  # AWS Config; the SourceAccount condition prevents cross-account
  # writes from impersonating us.
  statement {
    sid    = "AWSConfigBucketDelivery"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.config.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/Config/*"]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "config" {
  bucket = aws_s3_bucket.config.id
  policy = data.aws_iam_policy_document.config_bucket.json
}

resource "aws_s3_bucket_lifecycle_configuration" "config" {
  bucket = aws_s3_bucket.config.id

  depends_on = [aws_s3_bucket_versioning.config]

  rule {
    id     = "tier-and-expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ===========================================================================
# AWS Config service role
#
# AWS Config assumes this role to call DescribeConfigurationRecorders,
# read resource state, and write to the delivery-channel S3 bucket.
# The managed policy `AWSConfigRole` (now AWS_ConfigRole) is the
# canonical attachment; AWS keeps it current as new resource types
# become Config-recordable so we do not have to chase the schema.
# ===========================================================================

data "aws_iam_policy_document" "config_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "config" {
  name               = "${local.name_prefix}-config"
  assume_role_policy = data.aws_iam_policy_document.config_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "config_managed" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWS_ConfigRole"
}

# ===========================================================================
# AWS Config recorder + delivery channel
#
# `aws_config_configuration_recorder` declares the recorder; it does
# not start it. The recorder begins emitting (and therefore billing)
# only when `aws_config_configuration_recorder_status.is_enabled` is
# true. We default to false (var.enable_config) so an unattended apply
# does not start incurring per-rule-evaluation charges.
#
# `recording_group { all_supported = true include_global_resource_types
# = true }` records the union of every region-specific resource type
# AWS Config supports, plus IAM and other global resources. This is the
# widest net possible; tightening would require an explicit
# `resource_types` list, which AWS keeps growing.
# ===========================================================================

resource "aws_config_configuration_recorder" "this" {
  name     = "${local.name_prefix}-config"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${local.name_prefix}-config"
  s3_bucket_name = aws_s3_bucket.config.id

  # Snapshot delivery on the AWS-default 24-hour cadence. Set
  # explicitly so plan output is deterministic across provider
  # versions.
  snapshot_delivery_properties {
    delivery_frequency = "TwentyFour_Hours"
  }

  # The delivery channel cannot reference a recorder that does not
  # yet exist, and the recorder cannot run without a delivery channel
  # bound to it. Terraform handles ordering automatically via the
  # implicit dependency on the recorder name; an explicit depends_on
  # makes that contract visible in plan output.
  depends_on = [
    aws_config_configuration_recorder.this,
    aws_s3_bucket_policy.config,
  ]
}

resource "aws_config_configuration_recorder_status" "this" {
  name       = aws_config_configuration_recorder.this.name
  is_enabled = var.enable_config

  # Cannot start the recorder before the delivery channel exists.
  depends_on = [aws_config_delivery_channel.this]
}

# ===========================================================================
# AWS Config managed rules
#
# Free-to-deploy, opinionated baseline. Adding a rule is one line in
# `var.config_rules`. Each rule evaluates against the recorder's state
# stream; rule evaluations themselves are billed at $0.001 each.
#
# Rule selection rationale:
# - s3-bucket-public-read-prohibited: catches the most common S3 misconfiguration.
# - iam-password-policy: enforces a strong password policy on console users.
# - incoming-ssh-disabled: flags any security group with 0.0.0.0/0 ingress on port 22 (AWS-managed identifier `INCOMING_SSH_DISABLED`).
# ===========================================================================

resource "aws_config_config_rule" "managed" {
  for_each = var.config_rules

  name = each.value

  source {
    owner             = "AWS"
    source_identifier = upper(replace(each.value, "-", "_"))
  }

  # Rules cannot exist without a recorder bound; explicit dependency
  # avoids the race where the rule create tries to attach to a
  # not-yet-created recorder.
  depends_on = [aws_config_configuration_recorder.this]

  tags = local.common_tags
}

# ===========================================================================
# Amazon GuardDuty
#
# Detector resource exists in every plan; the `enable` flag controls
# whether the detector ingests the Foundational data sources
# (CloudTrail management events, VPC Flow Logs, DNS logs). All three
# are included in the base GuardDuty price ($1/M CloudTrail events,
# $1/GB Flow Logs, $4/M DNS queries at us-east-1 list pricing) and
# typical dev volumes are sub-dollar per month.
#
# Optional add-on data sources we deliberately omit:
# - S3 Protection: $0.085 per 100k S3 events. Not yet needed; flip on
#   when audio-uploads ingestion volume warrants it.
# - EKS Audit Logs: we do not run EKS in dev.
# - Malware Protection for EC2: $0.10/GB scanned + $0.05/GB stored;
#   only meaningful once the GPU stream/batch instances are in use.
# - RDS Login Events: $0.40/M auth events; not yet needed.
# - Lambda Network Activity: $1/M invocations; flip on when the event
#   router and other Lambdas reach steady-state volume.
#
# `finding_publishing_frequency = "FIFTEEN_MINUTES"` minimizes
# detection-to-alert latency. The default is 6 hours, which is too
# loose for a system that wants to react to incidents fast.
# ===========================================================================

resource "aws_guardduty_detector" "this" {
  enable                       = var.enable_guardduty
  finding_publishing_frequency = "FIFTEEN_MINUTES"

  tags = local.common_tags
}

# ===========================================================================
# AWS Security Hub
#
# Security Hub has no enable/disable flag at the API level: presence
# of the `aws_securityhub_account` resource enables the service for
# the account in the configured region. We gate via `count` so a
# `var.enable_security_hub = false` apply does not provision the
# account-level resource at all. Disabling later is a destroy on the
# next apply, which is the only API path AWS exposes.
#
# Two industry-standard standards are subscribed (also count-gated):
# - AWS Foundational Security Best Practices: AWS-curated checks
#   that align with their Well-Architected security pillar.
# - CIS AWS Foundations Benchmark v1.4.0: third-party (CIS) checks
#   that map cleanly to common compliance frameworks.
#
# Each enabled check costs $0.0010 per evaluation; both standards
# together evaluate roughly 200 checks per account-region. Steady
# state at dev volumes is well under $1/month.
#
# TODO(post-MVP): consider subscribing to NIST SP 800-53 Rev. 5 once
# Phil's portfolio narrative starts touching federal-style compliance
# claims.
# ===========================================================================

resource "aws_securityhub_account" "this" {
  count = var.enable_security_hub ? 1 : 0
}

resource "aws_securityhub_standards_subscription" "aws_foundational" {
  count = var.enable_security_hub ? 1 : 0

  standards_arn = "arn:${data.aws_partition.current.partition}:securityhub:${data.aws_region.current.region}::standards/aws-foundational-security-best-practices/v/1.0.0"

  depends_on = [aws_securityhub_account.this]
}

resource "aws_securityhub_standards_subscription" "cis" {
  count = var.enable_security_hub ? 1 : 0

  standards_arn = "arn:${data.aws_partition.current.partition}:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.4.0"

  depends_on = [aws_securityhub_account.this]
}
