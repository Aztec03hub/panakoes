locals {
  common_tags = {
    Project     = var.project_name
    Environment = "shared"
    ManagedBy   = "terraform"
    Module      = "bootstrap"
  }
}

# Random suffix to prevent global S3 bucket-name collisions.
resource "random_id" "suffix" {
  byte_length = 4
}

# KMS key encrypting the Terraform state bucket.
resource "aws_kms_key" "tf_state" {
  description             = "KMS key for Panakoes Terraform state encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = local.common_tags
}

resource "aws_kms_alias" "tf_state" {
  name          = "alias/panakoes-tf-state"
  target_key_id = aws_kms_key.tf_state.key_id
}

# S3 bucket holding remote Terraform state for every other config.
resource "aws_s3_bucket" "tf_state" {
  bucket = "${var.project_name}-tf-state-${random_id.suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.tf_state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle rules: state versioning generates a lot of noise; we don't
# need every historical version forever. Transition to STANDARD_IA
# after 30 days, delete after 90 days.
resource "aws_s3_bucket_lifecycle_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  # Versioning must be enabled before lifecycle rules referencing
  # noncurrent versions are accepted.
  depends_on = [aws_s3_bucket_versioning.tf_state]

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

# State locking now uses S3-native conditional writes via the
# `use_lockfile = true` backend argument (Terraform 1.10+). The
# legacy DynamoDB lock table previously declared here was retired
# 2026-05-09 after every backend (17 applied modules + 3 deferred)
# was migrated to use_lockfile in PR #162 and verified clean via
# `terraform plan` end-to-end. Issue #153 tracks the migration; this
# decommission closes it.
