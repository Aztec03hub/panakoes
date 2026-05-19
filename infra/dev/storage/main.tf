locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "storage"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Consolidated app-data CMK ARN (W2-T1, PR #365). Replaces the
  # per-bucket aws_kms_key.audio_uploads / .transcripts CMKs at the SSE
  # layer; the old aws_kms_key resources are intentionally left in
  # place so W2-T7 (orchestrator-only) can schedule them for deletion
  # via AWS CLI in a separate step.
  app_data_kms_key_arn = data.terraform_remote_state.kms.outputs.app_data_key_arn
}

# Caller identity is needed to scope the log-archive KMS key policy
# back to the account root and to constrain VPC Flow Logs delivery to
# this account via the aws:SourceAccount condition key.
data "aws_caller_identity" "current" {}

# Consolidated KMS module remote state (W2-T1). Surfaces the
# `panakoes/app-data` and `panakoes/logs` CMK ARNs so this module can
# stop using its per-bucket keys at the SSE layer.
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Random suffixes
#
# Each bucket gets its own random suffix so that even if one bucket is
# destroyed and recreated the others keep their stable names. The
# 4-byte hex pattern matches the bootstrap module.
# ---------------------------------------------------------------------------

resource "random_id" "audio_uploads_suffix" {
  byte_length = 4
}

resource "random_id" "transcripts_suffix" {
  byte_length = 4
}

resource "random_id" "log_archive_suffix" {
  byte_length = 4
}

# ===========================================================================
# Bucket 1: audio-uploads
#
# Raw audio files clients upload via the Ingestion API. CORS is enabled
# for the public-facing domains so browser-based uploads can PUT to
# pre-signed URLs directly. Current versions are kept indefinitely;
# the archived-audio lifecycle lives in a future bucket so this one
# stays focused on hot-path uploads. Noncurrent versions tier to IA at
# 30 days and expire at 90 days to keep versioning cost bounded.
# ===========================================================================

resource "aws_kms_key" "audio_uploads" {
  description             = "KMS key for the dev audio-uploads S3 bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = local.common_tags
}

resource "aws_kms_alias" "audio_uploads" {
  name          = "alias/${local.name_prefix}-audio-uploads"
  target_key_id = aws_kms_key.audio_uploads.key_id
}

resource "aws_s3_bucket" "audio_uploads" {
  bucket = "${local.name_prefix}-audio-uploads-${random_id.audio_uploads_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      # W2-T2: migrated from aws_kms_key.audio_uploads.arn to the
      # consolidated panakoes/app-data CMK. The per-bucket key resource
      # is retained for W2-T7 (CMK retirement, orchestrator-only step).
      kms_master_key_id = local.app_data_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = [
      "https://lafayettelabs.com",
      "https://panakoes.com",
      "https://app.panakoes.com",
    ]
    allowed_headers = [
      "Content-Type",
      "Content-Length",
      "Authorization",
    ]
    max_age_seconds = 3000
  }
}

data "aws_iam_policy_document" "audio_uploads_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.audio_uploads.arn,
      "${aws_s3_bucket.audio_uploads.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id
  policy = data.aws_iam_policy_document.audio_uploads_tls_only.json
}

resource "aws_s3_bucket_lifecycle_configuration" "audio_uploads" {
  bucket = aws_s3_bucket.audio_uploads.id

  # Versioning must exist before lifecycle rules referencing
  # noncurrent versions are accepted.
  depends_on = [aws_s3_bucket_versioning.audio_uploads]

  rule {
    id     = "noncurrent-version-tiering"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ===========================================================================
# Bucket 2: transcripts
#
# Transcript JSON output from the transcription Lambda. Read by the
# API for authenticated client retrieval; no browser upload path so no
# CORS. Lifecycle keeps current versions forever, transitioning to IA
# at 60 days and to Glacier Instant Retrieval at 180 days. Transcripts
# are small (KB scale) and cheap to keep.
# ===========================================================================

resource "aws_kms_key" "transcripts" {
  description             = "KMS key for the dev transcripts S3 bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = local.common_tags
}

resource "aws_kms_alias" "transcripts" {
  name          = "alias/${local.name_prefix}-transcripts"
  target_key_id = aws_kms_key.transcripts.key_id
}

resource "aws_s3_bucket" "transcripts" {
  bucket = "${local.name_prefix}-transcripts-${random_id.transcripts_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      # W2-T2: migrated from aws_kms_key.transcripts.arn to the
      # consolidated panakoes/app-data CMK. The per-bucket key resource
      # is retained for W2-T7 (CMK retirement, orchestrator-only step).
      kms_master_key_id = local.app_data_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "transcripts_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.transcripts.arn,
      "${aws_s3_bucket.transcripts.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id
  policy = data.aws_iam_policy_document.transcripts_tls_only.json
}

resource "aws_s3_bucket_lifecycle_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  depends_on = [aws_s3_bucket_versioning.transcripts]

  rule {
    id     = "current-version-tiering"
    status = "Enabled"

    filter {}

    transition {
      days          = 60
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }
  }
}

# ===========================================================================
# Bucket 3: log-archive
#
# CloudWatch Logs archived for Athena queries (per ADR-016). Long
# retention because compliance and forensic value increase with time
# horizon. Tier aggressively to cold storage and expire at 7 years
# (2555 days) which matches typical compliance retention windows.
# ===========================================================================

data "aws_iam_policy_document" "log_archive_kms" {
  # Default root-account control of the key (standard AWS pattern; the
  # caller-identity root principal can manage the key via IAM policies
  # without this statement, AWS would otherwise auto-attach the
  # equivalent default policy).
  statement {
    sid    = "EnableIAMPermissions"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }

  # VPC Flow Logs delivers to S3 via delivery.logs.amazonaws.com; with
  # SSE-KMS on the destination bucket the delivery service must be
  # allowed kms:GenerateDataKey on this CMK or PutObject silently
  # fails. Encryption-context scoping limits use to this account.
  statement {
    sid    = "AllowVPCFlowLogsDelivery"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "log_archive" {
  description             = "KMS key for the dev log-archive S3 bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.log_archive_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "log_archive" {
  name          = "alias/${local.name_prefix}-log-archive"
  target_key_id = aws_kms_key.log_archive.key_id
}

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
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.log_archive.arn
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

  # VPC Flow Logs (delivery.logs.amazonaws.com) needs s3:PutObject and
  # s3:GetBucketAcl on the destination bucket. Required because the
  # network module ships flow logs here as of 2026-05-18; the
  # community VPC module does not provision this policy itself when
  # `flow_log_destination_type = "s3"`.
  statement {
    sid    = "AWSLogDeliveryWrite"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    actions = ["s3:PutObject"]

    resources = [
      "${aws_s3_bucket.log_archive.arn}/AWSLogs/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  statement {
    sid    = "AWSLogDeliveryAclCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    actions = [
      "s3:GetBucketAcl",
      "s3:ListBucket",
    ]

    resources = [aws_s3_bucket.log_archive.arn]
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
    id     = "compliance-tiering-and-expiry"
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
      days          = 180
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }
  }
}
