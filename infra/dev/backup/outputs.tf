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
  description = "ARN of the CMK encrypting the legacy dev backup vault (`aws_backup_vault.dev`). Required in IAM policies that grant kms:Decrypt to restore consumers reading recovery points from the legacy vault. W2-T3 parallel-vault migration is now LIVE via the consolidated outputs below; this output remains until the legacy vault is retired in a separate follow-up PR (no sooner than 365 days after the parallel vault begins receiving monthly recovery points)."
  value       = aws_kms_key.backup.arn
}

output "service_role_arn" {
  description = "ARN of the AWS Backup service role. Reuse in `aws_backup_selection` resources defined in other modules so the same identity executes every project backup."
  value       = aws_iam_role.backup.arn
}

# ---------------------------------------------------------------------------
# W2-T3 parallel vault outputs
#
# The parallel vault provisioned under the consolidated
# `alias/panakoes/app-data` CMK runs alongside the legacy vault during
# the cutover window. Downstream tooling that needs to read recovery
# points from the new vault (restore drills, cross-account copy
# targets, audit reports) consumes these outputs.
# ---------------------------------------------------------------------------

output "consolidated_vault_arn" {
  description = "ARN of the parallel dev backup vault encrypted under the consolidated `alias/panakoes/app-data` CMK (W2-T3). Use in IAM policies that grant cross-account or cross-region copy targets, and in restore tooling that lists vaults to query."
  value       = aws_backup_vault.consolidated.arn
}

output "consolidated_vault_name" {
  description = "Name of the parallel dev backup vault (`panakoes-dev-consolidated`). Useful for AWS CLI calls and for `target_vault_name` references in downstream backup plans."
  value       = aws_backup_vault.consolidated.name
}

output "consolidated_plan_arn" {
  description = "ARN of the parallel daily/monthly backup plan attached to the consolidated vault. Surfaces for cross-account auditors and tooling that lists project backup plans."
  value       = aws_backup_plan.consolidated.arn
}

output "consolidated_plan_id" {
  description = "ID of the parallel daily/monthly backup plan. Used by `aws_backup_selection` consumers in other modules that want to attach additional resources to the same plan."
  value       = aws_backup_plan.consolidated.id
}
