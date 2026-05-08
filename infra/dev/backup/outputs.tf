output "vault_arn" {
  description = "ARN of the panakoes-dev backup vault. Use in IAM policies that grant cross-account or cross-region copy targets."
  value       = aws_backup_vault.dev.arn
}

output "vault_name" {
  description = "Name of the panakoes-dev backup vault. Useful for AWS CLI calls and for `target_vault_name` references in downstream backup plans."
  value       = aws_backup_vault.dev.name
}

output "plan_arn" {
  description = "ARN of the panakoes-dev-daily-monthly backup plan. Surfaces for cross-account auditors and for downstream tooling that lists project backup plans."
  value       = aws_backup_plan.dev.arn
}

output "plan_id" {
  description = "ID of the panakoes-dev-daily-monthly backup plan. Used by aws_backup_selection consumers in other modules that want to attach additional resources to the same plan."
  value       = aws_backup_plan.dev.id
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the dev backup vault. Required in IAM policies that grant kms:Decrypt to restore consumers."
  value       = aws_kms_key.backup.arn
}

output "service_role_arn" {
  description = "ARN of the AWS Backup service role. Reuse in `aws_backup_selection` resources defined in other modules so the same identity executes every project backup."
  value       = aws_iam_role.backup.arn
}
