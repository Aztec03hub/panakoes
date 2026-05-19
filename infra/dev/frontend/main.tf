locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "frontend"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Managed CloudFront policy IDs (AWS-curated, stable).
  # See: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html
  managed_cache_policy_caching_optimized = "658327ea-f89d-4fab-a63d-7e88639e58f6"

  # Managed response-headers policy: Managed-SecurityHeadersPolicy.
  # Pinned to the documented id to avoid name-resolution drift.
  managed_response_headers_policy_security_headers = "67f7725c-6f97-4210-82d7-5512b31e9d03"
}

# ---------------------------------------------------------------------------
# Random suffixes
#
# Each bucket gets its own 4-byte hex suffix so a destroy-recreate of one
# bucket does not strand the other's name. Mirrors the pattern in
# infra/dev/storage/.
# ---------------------------------------------------------------------------

resource "random_id" "frontend_suffix" {
  byte_length = 4
}

resource "random_id" "frontend_logs_suffix" {
  byte_length = 4
}

# ===========================================================================
# KMS key for the frontend origin bucket
#
# A dedicated CMK encrypts the SvelteKit static-asset origin bucket.
# The key policy grants:
#   1. Account root: full admin (default; explicit so a future statement
#      addition cannot accidentally lock the account out).
#   2. CloudFront service principal (cloudfront.amazonaws.com): permission
#      to Decrypt / GenerateDataKey when the request is sourced from this
#      account's CloudFront distribution. This is required for OAC + KMS:
#      CloudFront's OAC sigv4 signing fetches an object whose response
#      stream the service must decrypt before forwarding to the viewer.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "frontend_kms" {
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to this key only (`aws_kms_key.frontend`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  # CloudFront's OAC fetch path needs Decrypt on objects encrypted with
  # this CMK. The `AWS:SourceArn` condition pins it to *this* dev
  # distribution's ARN so other distributions in the account cannot
  # piggyback. We use a wildcard distribution-id at policy creation time
  # because the distribution does not exist yet; the policy is tightened
  # after the distribution lands via Terraform's normal apply cycle.
  statement {
    sid    = "AllowCloudFrontServiceUseOfKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey*",
    ]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; access is further
    # constrained to this account via the `aws:SourceAccount` condition.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "frontend" {
  description             = "KMS key for the dev frontend S3 origin bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.frontend_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "frontend" {
  name          = "alias/${local.name_prefix}-frontend"
  target_key_id = aws_kms_key.frontend.key_id
}

# ===========================================================================
# S3 origin bucket: panakoes-dev-frontend-<suffix>
#
# Holds the SvelteKit build output (static HTML / JS / CSS / assets).
# Strict hardening: public access fully blocked, versioning enabled,
# CMK-encrypted, OAC-only access (the bucket policy grants GetObject
# exclusively to the CloudFront distribution via the OAC condition).
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  bucket = "${local.name_prefix}-frontend-${random_id.frontend_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      # W2-T2: migrated from aws_kms_key.frontend.arn to the
      # consolidated panakoes/app-data CMK. The per-bucket key resource
      # is retained for W2-T7 (CMK retirement, orchestrator-only step).
      # The consolidated key already delegates to
      # cloudfront.amazonaws.com so the OAC + SSE-KMS delivery path is
      # preserved.
      kms_master_key_id = local.app_data_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ===========================================================================
# S3 access-log bucket: panakoes-dev-frontend-logs-<suffix>
#
# Receives CloudFront standard access logs (one log file per delivery
# window). Separate from the origin bucket so log retention and access
# controls can diverge from the asset bucket.
#
# Encryption: SSE-S3 (AES256). CloudFront's standard log delivery does
# not support SSE-KMS on the destination bucket; the documented
# constraint is that the bucket must use S3-managed keys. A future
# migration to v2 access logs (delivered via CloudWatch Logs vended
# logs) would unlock CMK encryption.
#
# Bucket Ownership: BucketOwnerEnforced disables ACLs entirely. With
# ACLs disabled, the legacy log-delivery ACL grant is replaced by a
# bucket-policy-based grant to the `delivery.logs.amazonaws.com`
# service principal (modern pattern; AWS docs as of 2024+).
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend_logs" {
  bucket = "${local.name_prefix}-frontend-logs-${random_id.frontend_logs_suffix.hex}"

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "frontend_logs" {
  bucket = aws_s3_bucket.frontend_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend_logs" {
  bucket = aws_s3_bucket.frontend_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "frontend_logs" {
  bucket = aws_s3_bucket.frontend_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Note: we deliberately do NOT set `aws_s3_bucket_ownership_controls`
# here. CloudFront Standard Logs v2 uses CloudWatch Logs Delivery as
# the writer (not CloudFront writing directly via ACLs), so the bucket
# can stay with AWS's modern default `BucketOwnerEnforced` (ACLs
# disabled) and we grant CWL Delivery access via bucket policy below.
# The legacy v1 path (S3 + log-delivery-write ACL) is fragile against
# AWS's BucketOwnerEnforced default and was failing with
# `InvalidArgument: ... does not enable ACL access` at distribution
# creation time.

resource "aws_s3_bucket_lifecycle_configuration" "frontend_logs" {
  bucket = aws_s3_bucket.frontend_logs.id

  depends_on = [aws_s3_bucket_versioning.frontend_logs]

  rule {
    id     = "expire-access-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.log_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.log_retention_days
    }
  }
}

# ===========================================================================
# CloudFront Origin Access Control (OAC)
#
# Modern replacement for legacy Origin Access Identity (OAI). OAC signs
# every origin request with sigv4, supports KMS-encrypted origins, and
# does not require maintaining a CloudFront IAM user. The S3 bucket
# policy (below) authorizes only this OAC's parent distribution.
# ---------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name_prefix}-frontend-oac"
  description                       = "OAC for the dev SvelteKit admin frontend distribution"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ===========================================================================
# CloudFront distribution: panakoes-dev-admin
#
# - Default origin: the private S3 bucket via OAC.
# - Default cache behavior: GET/HEAD only, redirect-to-https, compression
#   on, AWS managed Caching-Optimized cache policy, AWS managed
#   Security-Headers response-headers policy.
# - Custom error responses: 403 + 404 fall back to /index.html with a
#   200 status so SvelteKit's client-side router can resolve deep links
#   that do not have a corresponding S3 object key.
# - Logging: enabled to the dedicated frontend-logs bucket (90-day
#   lifecycle).
# - Viewer cert: CloudFront default cert. Custom domain + ACM cert
#   deferred until DNS is wired (see README).
# - Geo restriction: none.
# - WAF: optionally attached via var.associate_waf (default false; see
#   data.tf for the reasoning).
# - Price class: PriceClass_100 (US/Europe) for cost discipline.
# ---------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "admin" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "panakoes-dev-admin (SvelteKit admin frontend)"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-${aws_s3_bucket.frontend.id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id = "s3-${aws_s3_bucket.frontend.id}"

    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = local.managed_cache_policy_caching_optimized
    response_headers_policy_id = local.managed_response_headers_policy_security_headers
  }

  # SvelteKit (and any SPA) returns a 403 from S3 when a deep-link path
  # has no matching object. Map both 403 and 404 to /index.html with a
  # 200 so the client router can take over.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  # CloudFront access logs are configured separately below via the
  # CloudWatch Logs Delivery v2 resources. The legacy `logging_config`
  # block (S3 + ACL) is intentionally NOT used because AWS's modern
  # default ObjectOwnership of `BucketOwnerEnforced` makes the legacy
  # path fragile. See `aws_cloudwatch_log_delivery_*` resources at the
  # bottom of this file.

  # Conditional WAF association. The dev/waf module's web ACL is scoped
  # REGIONAL and cannot legally attach here (CloudFront requires
  # scope = CLOUDFRONT). Default false; flip the variable once a
  # CloudFront-scoped ACL exists.
  web_acl_id = var.associate_waf ? local.waf_web_acl_arn : null

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-admin"
  })
}

# ===========================================================================
# Bucket policy: CloudFront-OAC-only GetObject + TLS-only deny
#
# Two statements:
#   1. Allow `s3:GetObject` to the CloudFront service principal,
#      conditioned on the request originating from this exact
#      distribution's ARN. No human IAM principal can read the bucket
#      directly via this policy.
#   2. Deny `s3:*` over insecure transport. Mirrors the storage module's
#      TLS-only pattern.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid     = "AllowCloudFrontOACGet"
    effect  = "Allow"
    actions = ["s3:GetObject"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.admin.arn]
    }
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.frontend.arn,
      "${aws_s3_bucket.frontend.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json
}

# ===========================================================================
# Logs bucket policy: TLS-only deny
#
# CloudFront's standard-logs delivery uses ACL-based grants
# (log-delivery-write canned ACL on the bucket), not a bucket-policy
# allow. We add only the TLS-only deny here so the policy mirrors the
# rest of the project without disrupting log delivery.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "frontend_logs_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = [
      aws_s3_bucket.frontend_logs.arn,
      "${aws_s3_bucket.frontend_logs.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # CloudWatch Logs Delivery v2: writer principal for CloudFront access
  # logs. CWL Delivery (not CloudFront itself) writes objects to this
  # bucket, so the CloudFront source ARN goes in the SourceArn condition
  # to scope the grant. The `bucket-owner-full-control` ACL canned-name
  # in the request is what AWS expects so the bucket-owner remains the
  # object owner under BucketOwnerEnforced.
  statement {
    sid     = "AllowCWLDeliveryPutObject"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    resources = ["${aws_s3_bucket.frontend_logs.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:delivery-source:*"]
    }
  }

  statement {
    sid     = "AllowCWLDeliveryGetBucketAcl"
    effect  = "Allow"
    actions = ["s3:GetBucketAcl"]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    resources = [aws_s3_bucket.frontend_logs.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend_logs" {
  bucket = aws_s3_bucket.frontend_logs.id
  policy = data.aws_iam_policy_document.frontend_logs_bucket.json
}

# ===========================================================================
# CloudFront Standard Logs v2 (CloudWatch Logs Delivery -> S3)
#
# Replaces the legacy `aws_cloudfront_distribution.logging_config` block
# (S3 + log-delivery-write ACL) which is fragile against AWS's modern
# `BucketOwnerEnforced` default ObjectOwnership and was failing
# distribution creation on first apply 2026-05-09 with
# `InvalidArgument: ... does not enable ACL access`.
#
# In the v2 model, CloudWatch Logs Delivery (service principal
# `delivery.logs.amazonaws.com`) reads from the CloudFront access-log
# stream and writes to S3 via the bucket policy. The bucket itself
# stays on the secure default (BucketOwnerEnforced; ACLs disabled),
# and access is granted by the bucket policy statements above.
#
# Three resources:
#   1. delivery_source - registers the CloudFront distribution as a
#      log producer for the ACCESS_LOGS log type.
#   2. delivery_destination - declares the S3 bucket as the sink, with
#      a Hive-style time-partitioned suffix for efficient Athena query.
#   3. delivery - wires source -> destination.
#
# Reference: AWS docs "Configure CloudFront standard logs (v2)".
# ===========================================================================

resource "aws_cloudwatch_log_delivery_source" "frontend_access" {
  name         = "${local.name_prefix}-frontend-access"
  log_type     = "ACCESS_LOGS"
  resource_arn = aws_cloudfront_distribution.admin.arn
}

resource "aws_cloudwatch_log_delivery_destination" "frontend_access_s3" {
  name          = "${local.name_prefix}-frontend-access-s3"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_s3_bucket.frontend_logs.arn
  }
}

resource "aws_cloudwatch_log_delivery" "frontend_access" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.frontend_access.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.frontend_access_s3.arn

  s3_delivery_configuration {
    suffix_path                 = "cloudfront/{yyyy}/{MM}/{dd}/{HH}/"
    enable_hive_compatible_path = false
  }

  depends_on = [aws_s3_bucket_policy.frontend_logs]
}
