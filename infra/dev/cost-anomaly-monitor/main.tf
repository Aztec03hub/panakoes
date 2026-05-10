locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "cost-anomaly-monitor"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # ===========================================================================
  # Default-Services-Monitor ARN (AWS-owned, auto-provisioned per account)
  #
  # AWS auto-provisions a DIMENSIONAL monitor on the SERVICE dimension
  # named `Default-Services-Monitor` on every new account. The default
  # account quota for DIMENSIONAL monitors is 1, so a second one fails:
  #   ValidationException: Limit exceeded on dimensional spend monitor creation
  # We therefore attach our subscription to the existing default monitor
  # rather than creating a parallel (forbidden) one. Refactor 2026-05-09;
  # see memory `aws_default_anomaly_monitor_collision.md`.
  #
  # Why a hardcoded local instead of a data source: the AWS provider
  # (~> 6.0) does NOT ship an `aws_ce_anomaly_monitors` data source, so
  # there is no portable lookup. The ARN below is account-scoped and
  # stable for the life of the account; if Panakoes ever runs in a second
  # AWS account, replace this local with the new account's
  # Default-Services-Monitor ARN (visible at:
  # https://console.aws.amazon.com/cost-management/home#/anomaly-detection/monitors)
  # or migrate to a data source if AWS adds one.
  # ===========================================================================
  default_services_monitor_arn = "arn:aws:ce::659225405128:anomalymonitor/5a038097-9041-4c84-8554-4b4b2ac0398a"
}

# ===========================================================================
# Cost Anomaly Subscription (IMMEDIATE email alerts above $5 impact)
#
# Subscribes to the AWS-owned `Default-Services-Monitor` (DIMENSIONAL on
# SERVICE) instead of provisioning our own. See the local above for why.
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
    local.default_services_monitor_arn,
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
