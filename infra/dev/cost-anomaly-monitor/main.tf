locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "cost-anomaly-monitor"
  }

  name_prefix = "${var.project_name}-${var.environment}"
}

# ===========================================================================
# Cost Anomaly Monitor (DIMENSIONAL on SERVICE)
#
# A DIMENSIONAL monitor on the SERVICE dimension fires when any single
# AWS service's spend deviates materially from its forecast. This is the
# most useful default for a small dev environment: it surfaces unexpected
# spend in any service without requiring per-service monitor wiring.
#
# Alternatives considered:
#   - CUSTOM monitor with a specific service or LINKED_ACCOUNT filter:
#     more targeted, but premature for a single-account dev env. Easy to
#     add later (see README.md "How to extend").
#   - DIMENSIONAL on LINKED_ACCOUNT: not useful in a single-account
#     setup; would only fire if total account spend deviated.
# ===========================================================================
resource "aws_ce_anomaly_monitor" "dimensional" {
  name              = "${local.name_prefix}-service-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"

  tags = local.common_tags
}

# ===========================================================================
# Cost Anomaly Subscription (IMMEDIATE email alerts above $5 impact)
#
# Frequency IMMEDIATE: each anomaly emits its own alert. DAILY/WEEKLY
# digests are too aggressive a filter for a dev env where total spend is
# low and a $20 anomaly is a real signal worth seeing the same day. In
# production we would raise the threshold AND switch to DAILY to batch.
#
# Threshold: total impact >= $5 USD. Low for dev intentionally; CE
# anomaly detection is unsupervised and benefits from operator feedback
# loops on real (small) anomalies before tuning the floor up.
#
# Subscriber type EMAIL: the email address receives an AWS-managed
# confirmation email on first apply. Phil MUST click confirm before any
# alerts deliver. This is the single post-apply manual step.
# ===========================================================================
resource "aws_ce_anomaly_subscription" "email" {
  name      = "${local.name_prefix}-service-anomaly-subscription"
  frequency = "IMMEDIATE"

  monitor_arn_list = [
    aws_ce_anomaly_monitor.dimensional.arn,
  ]

  subscriber {
    type    = "EMAIL"
    address = var.alert_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      values        = ["5"]
      match_options = ["GREATER_THAN_OR_EQUAL"]
    }
  }

  tags = local.common_tags
}
