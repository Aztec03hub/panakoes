output "monitor_arn" {
  description = "ARN of the DIMENSIONAL Cost Anomaly Monitor on the SERVICE dimension. cost-api's anomaly detector reads anomalies via CE's GetAnomalies API; presence of this monitor is what flips that endpoint from `[]` to real data."
  value       = aws_ce_anomaly_monitor.dimensional.arn
}

output "subscription_arn" {
  description = "ARN of the email Cost Anomaly Subscription. The subscriber email receives an AWS confirmation message on first apply; alerts only deliver after Phil clicks the confirmation link."
  value       = aws_ce_anomaly_subscription.email.arn
}
