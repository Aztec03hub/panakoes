locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "waf"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  acl_name    = "${local.name_prefix}-public-acl"

  # AWS WAF requires the destination CloudWatch log group name to
  # start with the literal prefix `aws-waf-logs-`. The
  # `aws_wafv2_web_acl_logging_configuration` resource will reject any
  # other name with `WAFLogDestinationPermissionIssueException`. The
  # task brief asked for `/aws/wafv2/panakoes-dev-public-acl`; we honor
  # the spirit of the name (clear ownership, environment, ACL) but
  # adapt the prefix to satisfy the WAF constraint. The README records
  # this deviation.
  log_group_name = "aws-waf-logs-${local.acl_name}"
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key
#
# A dedicated CMK encrypts the WAF log group. Putting WAF logs on a
# distinct key from the rest of the dev infrastructure means a
# compromise of, say, the audio-uploads key cannot decrypt traffic
# logs containing source IPs and request signatures. Rotation enabled
# (annual, AWS-managed). 7-day deletion window matches the dev secrets
# CMK pattern: long enough to recover from accidental destroy, short
# enough that a stranded alias does not block reuse.
#
# CloudWatch Logs is granted use of the key via a dedicated key policy
# statement that pins the principal to the regional logs service and
# the resource to this account's log group ARNs (least-privilege key
# delegation per the CloudWatch Logs encryption guide).
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "waf_kms" {
  # Default key admin statement: account root keeps full control. AWS
  # auto-applies this when no policy is supplied; making it explicit
  # prevents lockout if we add a second statement and forget the
  # default.
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.waf`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  # Allow CloudWatch Logs in this region to encrypt the WAF log group.
  # Conditioned on the log-group ARN so other unrelated log groups in
  # this region cannot piggyback on the key.
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
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; EncryptionContext
    # condition below pins use to the WAF log group ARN.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group_name}"]
    }
  }
}

resource "aws_kms_key" "waf" {
  description             = "KMS key for the dev WAF web ACL CloudWatch logs"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.waf_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "waf" {
  name          = "alias/${local.name_prefix}-waf"
  target_key_id = aws_kms_key.waf.key_id
}

# ---------------------------------------------------------------------------
# CloudWatch Logs log group for WAF traffic
#
# WAF requires a log group whose name starts with `aws-waf-logs-`
# (see locals comment above). Retention 30 days matches the project's
# default CloudWatch retention; long-term log archive is the job of
# `infra/dev/storage/`'s log-archive S3 bucket via subscription
# filters in a future module.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "waf" {
  name              = local.log_group_name
  retention_in_days = 30
  kms_key_id        = aws_kms_key.waf.arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# WAFv2 Regional Web ACL: panakoes-dev-public-acl
#
# Scope = REGIONAL because the public APIs front-door is an ALB or
# regional API Gateway (ADR-pending). CloudFront would require
# scope = CLOUDFRONT and a us-east-1 provider; we stay regional in
# us-east-1 to match the rest of the dev environment.
#
# Default action is `allow`. Each managed-rule-group is added with
# action `none` (the rule group's own actions stand) so the four AWS
# Managed Rule Groups handle their own block decisions. The
# rate-limit rule action is `block` directly. Visibility config is
# enabled on every rule so CloudWatch metrics flow per-rule, which is
# how we track false-positive rates and tune exclusions.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "public" {
  name        = local.acl_name
  description = "Front-door WAF for the dev Panakoes public APIs: Ingestion, Query, Auth."
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # ---------------------------------------------------------------------
  # Priority 1: AWS Managed Rules - Common Rule Set
  #
  # Broad OWASP-Top-10 protection. Two rules excluded:
  #   - `SizeRestrictions_BODY`: middleware-lib enforces body-size
  #     limits per route; the default 8KB cap fires false positives
  #     on legitimate audio-metadata uploads.
  #   - `GenericRFI_BODY`: looks for path-traversal markers in the
  #     body and trips on legitimate JSON payloads that contain URLs.
  # ---------------------------------------------------------------------
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

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "GenericRFI_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-common-rule-set"
      sampled_requests_enabled   = true
    }
  }

  # ---------------------------------------------------------------------
  # Priority 2: AWS Managed Rules - Known Bad Inputs
  #
  # Blocks request patterns AWS has tied to active exploit attempts
  # (Log4Shell, Spring4Shell, etc). Low false-positive surface; ship
  # in block mode from day one.
  # ---------------------------------------------------------------------
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

  # ---------------------------------------------------------------------
  # Priority 3: AWS Managed Rules - Amazon IP Reputation List
  #
  # Blocks IPs the AWS threat-intel team has classified as malicious
  # (botnets, anonymizers, scrapers known to abuse). Updated by AWS;
  # we just consume.
  # ---------------------------------------------------------------------
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

  # ---------------------------------------------------------------------
  # Priority 4: AWS Managed Rules - SQL Database
  #
  # SQL-injection signatures across query string, body, headers, and
  # cookies. The auth and ingestion services use parameterized queries
  # via Drizzle / SQLAlchemy, so this is defense-in-depth, not the
  # primary control.
  # ---------------------------------------------------------------------
  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-sqli"
      sampled_requests_enabled   = true
    }
  }

  # ---------------------------------------------------------------------
  # Priority 5: Per-IP rate limit (1000 req / 5 min)
  #
  # Burst protection against credential-stuffing, scraping, and
  # naive denial-of-service. WAF rate-based statements use a rolling
  # 5-minute window keyed by source IP; the 1000-request budget is
  # generous for real users (the chatty Ingestion API is dominated by
  # pre-signed-URL PUTs that hit S3 directly, not the WAF).
  #
  # Scope-down NotStatement exempts `/health` so internal liveness
  # probes from the load balancer never get throttled. Without the
  # exemption, a pod-restart loop could trip the limit and ALB stops
  # routing real traffic.
  # ---------------------------------------------------------------------
  rule {
    name     = "RateLimitPerIP"
    priority = 5

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = 1000
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300

        scope_down_statement {
          not_statement {
            statement {
              byte_match_statement {
                search_string         = "/health"
                positional_constraint = "STARTS_WITH"

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.acl_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # ---------------------------------------------------------------------
  # Priority 6: Geo-block (DEFERRED)
  #
  # TODO: enable once we have a documented threat-driven country list
  # and a privacy review on geographic discrimination of users. The
  # block below is the syntax skeleton; uncomment, fill the
  # `country_codes` list (ISO 3166-1 alpha-2), set priority and
  # action, and re-apply. Default-allow stays in effect while this
  # block is commented out.
  #
  # rule {
  #   name     = "GeoBlock"
  #   priority = 6
  #
  #   action {
  #     block {}
  #   }
  #
  #   statement {
  #     geo_match_statement {
  #       country_codes = ["KP", "IR", "SY", "CU"]
  #     }
  #   }
  #
  #   visibility_config {
  #     cloudwatch_metrics_enabled = true
  #     metric_name                = "${local.acl_name}-geo-block"
  #     sampled_requests_enabled   = true
  #   }
  # }
  # ---------------------------------------------------------------------

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
# header so JWTs and session cookies do not land in CloudWatch in
# plaintext. The body is not redacted because the rate-limit
# investigation often hinges on payload fingerprinting; if we add
# request-body logging at scale we will revisit.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl_logging_configuration" "public" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.public.arn

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
