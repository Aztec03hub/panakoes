output "config_bucket_arn" {
  description = "ARN of the AWS Config delivery-channel S3 bucket. Use in IAM policies for any future tooling that reads Config snapshots or history files."
  value       = aws_s3_bucket.config.arn
}

output "config_bucket_name" {
  description = "Name of the AWS Config delivery-channel S3 bucket. Useful for awscli debugging (`aws s3 ls s3://<name>/AWSLogs/<account>/Config/`)."
  value       = aws_s3_bucket.config.id
}

output "kms_key_arn" {
  description = "ARN of the dev security-tier KMS CMK. Encrypts the Config delivery-channel bucket today and is the intended key for future GuardDuty/Security Hub findings exports."
  value       = aws_kms_key.security.arn
}

output "kms_key_alias" {
  description = "Alias name of the security-tier CMK (alias/panakoes-dev-security)."
  value       = aws_kms_alias.security.name
}

output "recorder_arn" {
  description = "ARN of the AWS Config configuration recorder. Useful for cross-account aggregator wiring or for IAM policies that grant Config-control APIs (start-configuration-recorder, stop-configuration-recorder)."
  value       = "arn:${data.aws_partition.current.partition}:config:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:config-recorder/${aws_config_configuration_recorder.this.name}"
}

output "recorder_name" {
  description = "Name of the AWS Config configuration recorder."
  value       = aws_config_configuration_recorder.this.name
}

output "config_role_arn" {
  description = "ARN of the IAM service role AWS Config assumes to record state and write to the delivery-channel bucket."
  value       = aws_iam_role.config.arn
}

output "detector_id" {
  description = "GuardDuty detector ID. Always present in plan output even when var.enable_guardduty is false; the detector resource exists with `enable = false` so flipping the flag is a one-line update rather than a create-from-scratch."
  value       = aws_guardduty_detector.this.id
}

output "detector_arn" {
  description = "GuardDuty detector ARN. Use in IAM policies that grant cross-account access (master-member relationships) or in event-bridge rules that fan out findings."
  value       = aws_guardduty_detector.this.arn
}

output "security_hub_account_arn" {
  description = "ARN of the Security Hub account-level resource. Empty string when var.enable_security_hub is false; the resource is count-gated because the AWS API treats resource presence as the enable signal."
  value       = var.enable_security_hub ? aws_securityhub_account.this[0].arn : ""
}
