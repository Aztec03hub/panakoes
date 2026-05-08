output "log_group_arns" {
  description = "Map of service name to CloudWatch Log Group ARN. Downstream IAM policies grant per-service log:* using these."
  value       = { for s, lg in aws_cloudwatch_log_group.service : s => lg.arn }
}

output "log_group_names" {
  description = "Map of service name to CloudWatch Log Group name. Convenience companion to log_group_arns; some AWS APIs accept name where others want ARN."
  value       = { for s, lg in aws_cloudwatch_log_group.service : s => lg.name }
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting CloudWatch log groups and the log archive bucket. Required in IAM policies that grant kms:Decrypt to log consumers."
  value       = aws_kms_key.logs.arn
}

output "archive_bucket_name" {
  description = "Name of the long-term S3 log archive bucket. Athena queries point here once events ship out of CloudWatch Logs."
  value       = aws_s3_bucket.log_archive.id
}

output "archive_bucket_arn" {
  description = "ARN of the log archive S3 bucket."
  value       = aws_s3_bucket.log_archive.arn
}

output "log_archiver_role_arn" {
  description = "ARN of the IAM role CloudWatch Logs assumes to write to the archive bucket. The future subscription-filter wiring config will pass this role to the shipping primitive."
  value       = aws_iam_role.log_archiver.arn
}
