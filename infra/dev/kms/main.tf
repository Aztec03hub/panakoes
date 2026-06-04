locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "kms"
  }
}

# ===========================================================================
# Consolidated CMK: app-data
#
# Replaces 12 of the 19 existing service-specific CMKs in a follow-up PR
# (W2-T2..T6). Consumers: S3 buckets (audio uploads, transcripts,
# frontend, security logs, log archive), RDS (auth-db), SQS, SNS,
# Secrets Manager, ECR, Backup vault.
#
# The key policy grants `kms:*` to the account root (standard pattern;
# required so administrative IAM identities in this account can manage
# the key) and a scoped set of encrypt / decrypt / data-key / grant
# actions to the AWS service principals that need to use the key on
# behalf of resources in THIS account. The `kms:CallerAccount`
# condition prevents cross-account use even if a principal outside the
# account is somehow granted permission to call a service that uses the
# key.
#
# JWT signing and Terraform-state encryption are deliberately NOT
# included; see `providers.tf` header for the security separation
# rationale.
# ===========================================================================

resource "aws_kms_key" "app_data" {
  description             = "Consolidated CMK for Panakoes app-data encryption (S3 buckets, RDS, SQS, SNS, Secrets Manager, ECR, Backup). Replaces 12 service-specific CMKs as of 2026-05-18. JWT signing stays separate on its own CMK for security separation; tf-state stays separate as bootstrap key."
  enable_key_rotation     = true
  is_enabled              = true
  multi_region            = false
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.app_data_key_policy.json

  tags = merge(local.common_tags, {
    Purpose = "app-data"
  })
}

resource "aws_kms_alias" "app_data" {
  name          = "alias/panakoes/app-data"
  target_key_id = aws_kms_key.app_data.key_id
}

data "aws_iam_policy_document" "app_data_key_policy" {
  # Root account has full administrative control over the key. Standard
  # KMS pattern: without this statement, an IAM identity in the account
  # cannot manage the key even with `kms:*` granted via an identity
  # policy, because key policies are the primary access-control surface.
  statement {
    sid    = "EnableRootAccess"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions = ["kms:*"]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.app_data`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  # AWS service principals that read/write resources encrypted with this
  # key. Scoped to the encrypt/decrypt/data-key/grant set; conditioned
  # on `kms:CallerAccount` so the key cannot be used by these services
  # on behalf of a different AWS account.
  #
  # `cloudfront.amazonaws.com` is included because the frontend SPA
  # S3 bucket is fronted by CloudFront with Origin Access Control
  # (OAC); CloudFront needs `kms:Decrypt` to serve KMS-encrypted
  # objects.
  statement {
    sid    = "AllowAwsServiceUse"
    effect = "Allow"

    principals {
      type = "Service"
      identifiers = [
        "backup.amazonaws.com",
        "cloudfront.amazonaws.com",
        "ecr.amazonaws.com",
        "events.amazonaws.com",
        "rds.amazonaws.com",
        "s3.amazonaws.com",
        "secretsmanager.amazonaws.com",
        "sns.amazonaws.com",
        "sqs.amazonaws.com",
      ]
    }

    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
      "kms:CreateGrant",
    ]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.app_data`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  # CloudFront service-use statement. Added live on 2026-06-04 via
  # `aws kms put-key-policy` (emergency restore: the admin SPA's S3
  # objects were re-encrypted under app-data and CloudFront could not
  # decrypt). Codified here so Terraform matches live state. This is a
  # standalone statement (distinct Sid, `aws:SourceAccount` condition)
  # rather than folding CloudFront into `AllowAwsServiceUse` above,
  # because the live policy carries it as a separate statement; merging
  # would produce a spurious policy diff on plan.
  statement {
    sid    = "AllowCloudFrontServiceUseOfKey"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions = [
      "kms:GenerateDataKey*",
      "kms:Decrypt",
    ]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.app_data`);
    # the `aws:SourceAccount` condition pins use to this account.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# ===========================================================================
# Consolidated CMK: logs
#
# Replaces 5 of the 19 existing service-specific CMKs in a follow-up PR
# (W2-T4). Consumers: CloudWatch Log groups for all services and for
# API Gateway / Step Functions / Lambda execution logs.
#
# The CloudWatch Logs service principal is region-scoped
# (`logs.us-east-1.amazonaws.com`, not `logs.amazonaws.com`); we build
# it from `data.aws_region.current.region` (the `.region` attribute is
# the provider 6.x replacement for the deprecated `.name` attribute).
#
# The `kms:EncryptionContext:aws:logs:arn` condition is the canonical
# CloudWatch-Logs-to-KMS pattern (see AWS docs:
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html).
# It scopes the key to log groups in THIS account and region.
# ===========================================================================

resource "aws_kms_key" "logs" {
  description             = "Consolidated CMK for Panakoes CloudWatch Logs groups. Replaces 5 service-specific log-group CMKs as of 2026-05-18."
  enable_key_rotation     = true
  is_enabled              = true
  multi_region            = false
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.logs_key_policy.json

  tags = merge(local.common_tags, {
    Purpose = "cloudwatch-logs"
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/panakoes/logs"
  target_key_id = aws_kms_key.logs.key_id
}

data "aws_iam_policy_document" "logs_key_policy" {
  statement {
    sid    = "EnableRootAccess"
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

  statement {
    sid    = "AllowCloudWatchLogsUse"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }

    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.logs`);
    # the `kms:EncryptionContext:aws:logs:arn` condition below pins
    # actual use to CloudWatch log groups in THIS account and region.
    # https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html
    resources = ["*"]

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }
}
