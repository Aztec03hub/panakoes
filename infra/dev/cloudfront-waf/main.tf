locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "cloudfront-waf"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  acl_name    = "${local.name_prefix}-cloudfront-acl"

  # AWS WAF requires destination CloudWatch log group names to begin
  # with the literal prefix `aws-waf-logs-`. The
  # `aws_wafv2_web_acl_logging_configuration` resource rejects any
  # other name with `WAFLogDestinationPermissionIssueException`. We
  # honor the brief's intent (`/aws/wafv2/panakoes-dev`) by preserving
  # the identifier in the suffix while satisfying the WAF constraint.
  log_group_name = "aws-waf-logs-${local.name_prefix}-cloudfront"
}

# ---------------------------------------------------------------------------
# Cross-config data sources
#
# The frontend distribution lives in `infra/dev/frontend/` and exposes
# its ID + ARN via that config's outputs. Pulling them via remote-state
# means we never hardcode the distribution id (which carries a random
# suffix) and we never need to mutate `infra/dev/frontend/` to wire the
# WAF association.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "frontend" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/frontend/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key for the WAF log group
#
# Dedicated CMK encrypts the CloudFront WAF log group. A separate key
# from the regional WAF's CMK keeps key-policy blast radius scoped per
# log group; a future compromise of the regional-WAF CMK cannot decrypt
# CloudFront traffic logs (and vice versa). Rotation enabled (annual,
# AWS-managed). 7-day deletion window mirrors the regional WAF module.
#
# CloudWatch Logs is granted use of the key via a dedicated key policy
# statement that pins the principal to the regional logs service and
# the resource to this specific log group's ARN (least-privilege key
# delegation per the CloudWatch Logs encryption guide).
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "waf_kms" {
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    resources = ["*"]
  }

  statement {
    sid    = "AllowCloudWatchLogsEncrypt"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]

    principals {
      type        = "Service"
      identifiers = ["logs.us-east-1.amazonaws.com"]
    }

    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group_name}"]
    }
  }
}

resource "aws_kms_key" "waf" {
  description             = "KMS key for the dev CloudFront WAF web ACL CloudWatch logs"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.waf_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "waf" {
  name          = "alias/${local.name_prefix}-cloudfront-waf"
  target_key_id = aws_kms_key.waf.key_id
}

# ---------------------------------------------------------------------------
# CloudWatch Logs log group for CloudFront WAF traffic
#
# Retention 30 days matches the project's default. Long-term archive
# is the job of `infra/dev/observability/`'s log-archive S3 bucket via
# a subscription filter in a future module.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "waf" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.waf.arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# WAFv2 CloudFront-scoped Web ACL: panakoes-dev-cloudfront-acl
#
# Default action is `allow`. Each AWS Managed Rule Group is attached
# with `override_action = none` so the rule group's own block actions
# stand. The custom rate-limit rule action is `block` directly.
# Visibility config is enabled on every rule so CloudWatch metrics
# flow per-rule, which is how we tune false-positive rates and decide
# what to count-mode vs block-mode in production.
#
# Why these three managed rule groups (and not the four the regional
# WAF runs):
#
#   - CommonRuleSet: broad OWASP-Top-10 coverage. Sensible default for
#     an admin SPA + its async fetches.
#   - KnownBadInputsRuleSet: signatures AWS ties to active exploit
#     campaigns (Log4Shell, Spring4Shell, etc). Cheap, low FP rate,
#     ship in block mode from day one.
#   - AmazonIpReputationList: AWS-curated malicious IPs (botnets,
#     anonymizers, scrapers known to abuse). Trivially worth the $1/mo.
#
# The regional ACL also runs SQLiRuleSet because the public API
# services accept query payloads. The admin SPA itself is static
# assets + bearer-authenticated JSON fetches, so the SQLi rule group
# would mostly inspect the JSON bodies of the fetches. Those fetches
# already pass through the regional WAF at the API Gateway layer where
# SQLiRuleSet IS attached; running it twice on the same payload would
# double-bill request inspection without adding security value. Add it
# here if we later front a SQL-touching endpoint at the CloudFront
# distribution directly (e.g. server-rendered admin pages).
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "cloudfront" {
  name        = local.acl_name
  description = "CloudFront-scoped WAF for the dev Panakoes admin SPA distribution."
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # -----------------------------------------------------------------
  # Priority 1: AWS Managed Rules - Common Rule Set
  #
  # Broad OWASP-Top-10 protection. No rule_action_override here: the
  # admin SPA never PUTs arbitrary bodies via CloudFront (uploads go
  # straight to the Ingestion API + presigned-URL S3 path, which does
  # NOT route through CloudFront), so the body-size and body-content
  # rules that we had to relax for the regional WAF do not generate
  # false positives here.
  # -----------------------------------------------------------------
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-common-rule-set"
      sampled_requests_enabled   = true
    }
  }

  # -----------------------------------------------------------------
  # Priority 2: AWS Managed Rules - Known Bad Inputs
  # -----------------------------------------------------------------
  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # -----------------------------------------------------------------
  # Priority 3: AWS Managed Rules - Amazon IP Reputation List
  # -----------------------------------------------------------------
  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  # -----------------------------------------------------------------
  # Priority 4: Per-IP rate limit
  #
  # WAF rate-based statements use a rolling 5-minute window keyed by
  # source IP. 2000 req / 5 min is the dashboard-friendly default
  # (see variables.tf for the sizing rationale). No scope-down
  # statement: a CloudFront distribution has no liveness-probe
  # contemporaneous with the viewer traffic (the ALB-style /health
  # probe does not exist at the CloudFront layer; CloudFront just
  # caches and forwards). Adding a scope-down here would only narrow
  # the budget, not protect a legitimate probe surface.
  # -----------------------------------------------------------------
  rule {
    name     = "RateLimitPerIP"
    priority = 4

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = var.rate_limit_per_5min
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = local.acl_name
    sampled_requests_enabled   = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Web ACL logging configuration
#
# Routes every request the ACL evaluates to the dedicated log group.
# `redacted_fields` masks the Authorization header and the Cookie
# header so bearer tokens and session cookies do not land in
# CloudWatch in plaintext. Body is not redacted; CloudFront requests
# from the admin SPA carry small JSON payloads at most, and being
# able to fingerprint a payload is essential to investigate a
# rate-limit or managed-rule trip.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl_logging_configuration" "cloudfront" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.cloudfront.arn

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}

# ---------------------------------------------------------------------------
# CloudFront distribution association
#
# CloudFront does not use `aws_wafv2_web_acl_association` (which is
# REGIONAL-only). Instead, the distribution carries a `web_acl_id`
# attribute that points at the ACL's ARN. The `infra/dev/frontend/`
# module already wires this attribute through `var.associate_waf` and
# a remote-state lookup; flipping that variable to `true` and pointing
# its remote-state at THIS module's state attaches the ACL with an
# in-place update on the distribution (no replacement).
#
# The association itself is therefore declared in `infra/dev/frontend/`
# rather than here. This module only owns the ACL, its logging, and
# its key.
# ---------------------------------------------------------------------------
