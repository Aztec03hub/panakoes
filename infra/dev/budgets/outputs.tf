output "budget_arns" {
  description = "Map of budget logical name to ARN for every budget provisioned by this module (account-wide, service-specific, tag-scoped)."
  value = {
    account_monthly       = aws_budgets_budget.account_monthly.arn
    ec2_monthly           = aws_budgets_budget.ec2_monthly.arn
    aurora_monthly        = aws_budgets_budget.aurora_monthly.arn
    bedrock_monthly       = aws_budgets_budget.bedrock_monthly.arn
    cloudfront_s3_monthly = aws_budgets_budget.cloudfront_s3_monthly.arn
    project_tag_monthly   = aws_budgets_budget.project_tag_monthly.arn
  }
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic that receives budget threshold notifications. Future Slack / PagerDuty / ChatBot integrations attach as additional subscribers."
  value       = aws_sns_topic.budget_alerts.arn
}

output "email_subscription_arn" {
  description = "ARN of the email subscription on the budget-alerts SNS topic. Subscription stays in `PendingConfirmation` state until the operator clicks the AWS Notification confirmation link sent to var.alert_email on first apply."
  value       = aws_sns_topic_subscription.budget_alerts_email.arn
}

output "cloudwatch_alarm_arn" {
  description = "ARN of the CloudWatch alarm that trips on the 100% ACTUAL account-wide threshold (proxied via the SNS topic's NumberOfMessagesPublished metric)."
  value       = aws_cloudwatch_metric_alarm.budget_100pct_actual.arn
}
