locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "budgets"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # ===========================================================================
  # AWS Budgets cost_filter SERVICE dimension values (canonical)
  #
  # AWS Budgets requires the EXACT Cost Explorer SERVICE dimension string
  # (e.g. "Amazon Elastic Compute Cloud - Compute", NOT "EC2"). Verified
  # against `aws ce get-dimension-values --dimension SERVICE` on
  # 2026-05-11. Bedrock and CloudFront did not yet appear in this
  # account's CE dimension list (zero historic spend) but the canonical
  # AWS-published strings are stable account-wide; Budgets accepts them
  # ahead of first spend and starts evaluating once usage lands.
  # ===========================================================================
  service_ec2        = "Amazon Elastic Compute Cloud - Compute"
  service_aurora_rds = "Amazon Relational Database Service"
  service_bedrock    = "Amazon Bedrock"
  service_cloudfront = "Amazon CloudFront"
  service_s3         = "Amazon Simple Storage Service"
}

# ===========================================================================
# SNS topic for budget alerts (fan-out hub)
#
# Every budget threshold notification posts to this topic in addition to
# direct EMAIL subscribers. The topic exists so future Slack / PagerDuty
# / ChatBot integrations are a subscriber swap rather than a budget
# resource change. AWS Budgets requires the topic policy to allow
# `budgets.amazonaws.com` Publish; the policy below scopes Publish to
# our own account-id source ARNs so only Panakoes budgets in this
# account can post.
# ===========================================================================
resource "aws_sns_topic" "budget_alerts" {
  name = "${local.name_prefix}-budget-alerts"
  tags = local.common_tags
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "budget_alerts_topic" {
  statement {
    sid    = "AllowAWSBudgetsPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.budget_alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:budgets::${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts_topic.json
}

resource "aws_sns_topic_subscription" "budget_alerts_email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ===========================================================================
# CloudWatch alarm fired by the 100% ACTUAL account-wide threshold
#
# Budgets cannot directly target CloudWatch alarms; the canonical pattern
# is SNS-topic fan-out where the alarm subscribes to the same topic.
# This alarm sits in INSUFFICIENT_DATA at steady state and trips when
# the SNS topic receives a NumberOfMessagesPublished spike (a proxy for
# "a budget notification fired"). Documented as a coarse signal; for
# per-threshold fidelity, switch to a Lambda subscriber that parses the
# SNS message body and emits a custom metric per budget name.
# ===========================================================================
resource "aws_cloudwatch_metric_alarm" "budget_100pct_actual" {
  alarm_name          = "${local.name_prefix}-budget-100pct-actual"
  alarm_description   = "Trips when the budgets SNS topic publishes a 100% ACTUAL notification for the account-wide ${local.name_prefix} budget."
  namespace           = "AWS/SNS"
  metric_name         = "NumberOfMessagesPublished"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TopicName = aws_sns_topic.budget_alerts.name
  }

  alarm_actions = [aws_sns_topic.budget_alerts.arn]
  ok_actions    = [aws_sns_topic.budget_alerts.arn]

  tags = local.common_tags
}

# ===========================================================================
# Account-wide monthly budget ($100/mo default)
#
# Four notifications:
#   50% ACTUAL     -> email
#   80% ACTUAL     -> email + SNS
#   80% FORECASTED -> email + SNS (proactive warning)
#  100% ACTUAL     -> email + SNS (which also drives the CW alarm above)
#
# `cost_types` excludes credits/refunds so AWS Activate credits don't
# mask real spend regressions.
# ===========================================================================
resource "aws_budgets_budget" "account_monthly" {
  name              = "${local.name_prefix}-account-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.account_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_types {
    include_credit             = false
    include_refund             = false
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_amortized              = false
    use_blended                = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}

# ===========================================================================
# Service-specific monthly budgets
#
# Each uses cost_filter { name = "Service" } with the canonical CE
# SERVICE dimension string. Two ACTUAL thresholds per budget (80%, 100%)
# with email subscribers; SNS topic added at 100% so a per-service
# overrun still drives the CloudWatch alarm.
# ===========================================================================

resource "aws_budgets_budget" "ec2_monthly" {
  name              = "${local.name_prefix}-ec2-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.ec2_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_filter {
    name   = "Service"
    values = [local.service_ec2]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}

resource "aws_budgets_budget" "aurora_monthly" {
  name              = "${local.name_prefix}-aurora-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.aurora_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_filter {
    name   = "Service"
    values = [local.service_aurora_rds]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}

resource "aws_budgets_budget" "bedrock_monthly" {
  name              = "${local.name_prefix}-bedrock-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.bedrock_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_filter {
    name   = "Service"
    values = [local.service_bedrock]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}

resource "aws_budgets_budget" "cloudfront_s3_monthly" {
  name              = "${local.name_prefix}-cloudfront-s3-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.cloudfront_s3_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_filter {
    name   = "Service"
    values = [local.service_cloudfront, local.service_s3]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}

# ===========================================================================
# Per-environment tag-scoped budget
#
# Filters spend to resources tagged `Project=panakoes`. Once future
# staging / prod environments share this AWS account, each environment
# carries its own `Environment=<env>` tag and rolls up under this
# Project-level budget. To split per-environment, clone this resource
# with cost_filter { name = "TagKeyValue"; values =
# ["user:Environment$staging"] }.
#
# The TagKeyValue cost_filter format is `user:<TagKey>$<TagValue>`; the
# `user:` prefix is mandatory for user-defined cost-allocation tags.
# The `Project` tag MUST be activated as a cost-allocation tag in
# Billing -> Cost allocation tags before this budget will see any spend
# (one-click activation; takes up to 24h to populate historic data,
# real-time going forward). Documented in README.
# ===========================================================================
resource "aws_budgets_budget" "project_tag_monthly" {
  name              = "${local.name_prefix}-project-tag-monthly"
  budget_type       = "COST"
  limit_amount      = tostring(var.project_tag_budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$${var.project_name}"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}
