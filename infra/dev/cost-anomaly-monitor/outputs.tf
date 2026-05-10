output "subscription_arn" {
  description = "ARN of the email Cost Anomaly Subscription. The subscriber email receives an AWS confirmation message on first apply; alerts only deliver after Phil clicks the confirmation link."
  value       = aws_ce_anomaly_subscription.email.arn
}
