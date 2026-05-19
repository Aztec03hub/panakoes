# Consolidated KMS module remote state (W2-T1, PR #365). Surfaces the
# `panakoes/logs` CMK ARN used by every CloudWatch log group below as
# of W2-T4. The module-local aws_kms_key.logs resource is retained for
# W2-T7 retirement (orchestrator-only step).
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "observability"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Per-service log group fan-out. Adding a service is a one-line change
  # here; the for_each on log_groups, metric_filters, and any future
  # subscription_filters keeps the resource graph in lockstep.
  services = [
    "admin-api",
    "auth",
    "billing",
    "cost-api",
    "event-router",
    "gpu-spawner",
    "health-aggregator",
    "ingestion-api",
    "notification",
    "query-api",
    "session-manager",
    "summarization",
    "transcriber-batch",
    "transcriber-stream",
  ]

  log_group_name_for = { for s in local.services : s => "/${var.project_name}/${var.environment}/${s}" }

  # Consolidated logs CMK ARN (W2-T4). Every aws_cloudwatch_log_group
  # in this module migrates onto this key; the local aws_kms_key.logs
  # resource is retained for W2-T7 retirement. The consolidated key's
  # policy grants logs.us-east-1.amazonaws.com use under a broad
  # `log-group:*` EncryptionContext condition, which covers every
  # `/panakoes/dev/*` log group below.
  logs_kms_key_arn = data.terraform_remote_state.kms.outputs.logs_key_arn
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Log archive bucket suffix
#
# A dedicated random suffix avoids name collisions with any past or
# future bucket sharing the prefix. 4-byte hex matches the pattern used
# by `infra/dev/storage/`.
# ---------------------------------------------------------------------------
resource "random_id" "log_archive_suffix" {
  byte_length = 4
}

# ===========================================================================
# KMS CMK for CloudWatch Logs encryption
#
# Dedicated key so log access can be granted independently of the
# storage-tier keys. Rotation enabled. Deletion window intentionally set
# to 7 days for this dev key (not the 30-day default used elsewhere) per
# the module brief: faster teardown for an env we expect to rebuild.
# Production observability would flip to 30 days.
#
# The key policy MUST grant CloudWatch Logs permission to use the key
# for encrypt / describe operations. Without this stanza, the
# associated log groups silently fail to encrypt and CloudWatch surfaces
# a "kms:GenerateDataKey" denial only when the first event arrives.
# ===========================================================================
resource "aws_kms_key" "logs" {
  description             = "KMS CMK for ${local.name_prefix} CloudWatch log group encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = data.aws_iam_policy_document.logs_kms_policy.json

  tags = local.common_tags
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${local.name_prefix}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

data "aws_iam_policy_document" "logs_kms_policy" {
  # Account root retains admin so IAM principals with sufficient
  # privilege can manage the key. Standard CMK policy idiom.
  statement {
    sid    = "EnableRootAccountAdmin"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions = ["kms:*"]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.logs`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  # CloudWatch Logs uses GenerateDataKey + Decrypt + Describe to wrap
  # the per-log-group data key. Scoped to the regional logs service
  # principal and constrained to log groups that match our naming
  # prefix via the EncryptionContext condition.
  statement {
    sid    = "AllowCloudWatchLogsUseOfKey"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }

    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; the EncryptionContext
    # ArnLike condition pins use to /panakoes/<env>/* log groups.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project_name}/${var.environment}/*"]
    }
  }
}

# ===========================================================================
# Per-service CloudWatch Log Groups
#
# Each service writes to `/panakoes/dev/<service>`. 7-day retention in
# dev (was 30) as of the 2026-05-18 tier-1 cost cut; logs older than
# 7 days live in the S3 archive, queried via Athena. KMS-encrypted
# with the dedicated logs CMK.
# ===========================================================================
resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(local.services)

  name              = local.log_group_name_for[each.key]
  retention_in_days = 7
  # W2-T4: migrated from aws_kms_key.logs.arn to the consolidated
  # panakoes/logs CMK. The local key resource is retained for W2-T7
  # retirement (orchestrator-only step).
  kms_key_id = local.logs_kms_key_arn

  tags = merge(local.common_tags, {
    Service = each.key
  })
}

# ===========================================================================
# Per-service metric filters
#
# Each filter increments `panakoes/dev/<service>.errors_total` whenever
# a log event matches `ERROR` or `error_code`. The OR pattern uses
# CloudWatch Logs filter syntax: `?ERROR ?error_code`. Caveats:
#   - The match is substring-based on raw event text, so JSON logs
#     containing the literal token "ERROR" or "error_code" will trip
#     it. That is the desired behavior here; structured Python logs
#     emit `"levelname": "ERROR"` and structured TS logs emit
#     `"error_code": "..."`.
#   - The metric is a CloudWatch custom metric in namespace
#     `panakoes/dev` with metric name `<service>.errors_total`.
#     Alarms attach to it in a follow-up config.
# ===========================================================================
resource "aws_cloudwatch_log_metric_filter" "service_errors" {
  for_each = toset(local.services)

  name           = "${local.name_prefix}-${each.key}-errors"
  log_group_name = aws_cloudwatch_log_group.service[each.key].name
  pattern        = "?ERROR ?error_code"

  metric_transformation {
    name      = "${each.key}.errors_total"
    namespace = "${var.project_name}/${var.environment}"
    value     = "1"
    unit      = "Count"

    default_value = "0"
  }
}

# ===========================================================================
# S3 log archive bucket
#
# Mirrors the hardening pattern from `infra/dev/storage/log-archive`
# (versioning, public access blocked, TLS-only policy, KMS-encrypted)
# but with the lifecycle tiering specified in this module's brief:
# STANDARD -> IA at 30d -> GLACIER_IR at 90d -> DEEP_ARCHIVE at 365d,
# with an abort-multipart-upload rule at 7 days.
#
# Objects are encrypted with the same logs CMK so a single key grants
# end-to-end visibility into the log pipeline (Logs -> Firehose -> S3).
# ===========================================================================
resource "aws_s3_bucket" "log_archive" {
  bucket = "${local.name_prefix}-log-archive-${random_id.log_archive_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      # W2-T4: migrated from aws_kms_key.logs.arn to the consolidated
      # panakoes/logs CMK. The log_archiver IAM role's identity
      # policy (see aws_iam_role_policy.log_archiver below) was also
      # updated to grant kms:GenerateDataKey + kms:Decrypt on the new
      # key so SSE-KMS PutObject succeeds. The root-admin statement on
      # the consolidated key's policy delegates use to identity
      # policies in this account.
      kms_master_key_id = local.logs_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "log_archive_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.log_archive.arn,
      "${aws_s3_bucket.log_archive.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id
  policy = data.aws_iam_policy_document.log_archive_tls_only.json
}

resource "aws_s3_bucket_lifecycle_configuration" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  depends_on = [aws_s3_bucket_versioning.log_archive]

  rule {
    id     = "archive-tiering"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ===========================================================================
# IAM role for CloudWatch Logs to ship to the archive
#
# Trust policy targets the regional CloudWatch Logs service principal
# (`logs.us-east-1.amazonaws.com`). Permissions are scoped to the
# archive bucket only: `s3:PutObject` on the object key namespace and
# `s3:GetBucketAcl` on the bucket itself (CloudWatch Logs S3 export
# requires the bucket-acl read to verify the destination).
#
# This role is the credential the future subscription wiring will hand
# CloudWatch Logs (or whichever shipping primitive lands; see the
# deferred-Firehose note in README.md). Provisioning the role now lets
# the IAM review happen in this PR rather than in the wiring PR.
# ===========================================================================
data "aws_iam_policy_document" "log_archiver_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "log_archiver" {
  name               = "${local.name_prefix}-log-archiver"
  assume_role_policy = data.aws_iam_policy_document.log_archiver_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "log_archiver_permissions" {
  statement {
    sid       = "WriteArchiveObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.log_archive.arn}/*"]
  }

  statement {
    sid       = "ReadArchiveBucketAcl"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.log_archive.arn]
  }

  # The archiver needs to use the logs CMK to encrypt objects on PUT
  # and decrypt them on read (Athena, replays). Scoped to the single
  # logs key. W2-T4: now grants on the consolidated panakoes/logs CMK
  # instead of the module-local aws_kms_key.logs (which is retained
  # for W2-T7 retirement but no longer encrypts new objects).
  statement {
    sid    = "UseLogsCMK"
    effect = "Allow"
    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
    ]
    resources = [local.logs_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "log_archiver" {
  name   = "${local.name_prefix}-log-archiver-policy"
  role   = aws_iam_role.log_archiver.id
  policy = data.aws_iam_policy_document.log_archiver_permissions.json
}

# ---------------------------------------------------------------------------
# TODO (deferred to a follow-up PR): subscription wiring.
#
# This module deliberately stops at "log groups + archive bucket + IAM
# role" so the PR stays small and reviewable. The shipping path will
# be one of:
#
#   1. Kinesis Data Firehose delivery stream
#      (`panakoes-dev-log-archive-firehose`) with an S3 destination on
#      `aws_s3_bucket.log_archive`, plus per-log-group
#      `aws_cloudwatch_log_subscription_filter` resources targeting
#      the Firehose. Firehose buffers (size + interval) batch events
#      into Parquet-friendly object sizes for Athena. This is the
#      production path and the one the brief sketches.
#
#   2. Lambda forwarder (cheaper at very low volume, more wiring at
#      scale). Single Lambda function fans out events across services.
#
# When wiring lands, the Firehose role policy will additionally grant
# `kms:GenerateDataKey + kms:Decrypt` on `aws_kms_key.logs.arn` (the
# permission already exists on `aws_iam_role.log_archiver` for exactly
# this reason; the wiring PR can either reuse this role or split into
# a dedicated firehose role for blast-radius isolation).
# ---------------------------------------------------------------------------
