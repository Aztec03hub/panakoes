output "instance_arn" {
  description = "ARN of the RDS PostgreSQL auth-db instance. Required in any IAM policy that grants RDS API actions (Describe, Modify, etc.) to a consuming role."
  value       = aws_db_instance.auth_db.arn
}

output "instance_endpoint" {
  description = "Connection endpoint (hostname) for the instance. Use this for read-write traffic from the auth service. Single-AZ in dev so there is no separate reader endpoint. Output name `instance_endpoint` (vs the Aurora module's `cluster_endpoint`) so consumers can branch their DSN-construction code on which module they read from."
  value       = aws_db_instance.auth_db.endpoint
}

output "instance_address" {
  description = "DNS name (without port) of the instance. Same hostname as `instance_endpoint` minus the `:5432` suffix. Some clients prefer host+port split."
  value       = aws_db_instance.auth_db.address
}

output "port" {
  description = "TCP port the instance listens on (5432 for Postgres). Surfaced as an output so the security group rule and the auth service connection string stay in sync without hardcoding the value in two places."
  value       = aws_db_instance.auth_db.port
}

output "security_group_id" {
  description = "ID of the instance security group. Consumers (ECS task SGs, Lambda VPC SGs) reference this in their own egress rules; today inbound is open from the VPC CIDR, but a future tightening pass will replace the CIDR rule with security-group-to-security-group references and this output becomes the source of truth for that wiring."
  value       = aws_security_group.auth_db_rds.id
}

output "kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting the instance's storage and Performance Insights data. Required for any consumer IAM policy that needs `kms:Decrypt` on RDS-encrypted snapshots or PI data. W2-T5: DEFERRED to a follow-up agent; the live RDS instance is still encrypted under the module-local aws_kms_key.auth_db_rds because flipping the kms_key_id forces an instance replacement and requires an out-of-band snapshot+restore."
  value       = aws_kms_key.auth_db_rds.arn
}

output "database_name" {
  description = "Name of the initial database created on instance bootstrap. Better-Auth's tables land here. Surface so consumers building DSNs do not hardcode the name in a second place."
  value       = aws_db_instance.auth_db.db_name
}

output "master_username" {
  description = "Master username configured on the instance. Same rationale as `database_name` -- single source of truth for downstream DSN construction."
  value       = aws_db_instance.auth_db.username
}

# ---------------------------------------------------------------------------
# W2-T5: outputs for the v2 instance (snapshot-restored, consolidated CMK)
#
# Downstream consumers (the secrets module's `database-url` placeholder,
# the cutover script in the README) read these. After the v1 retirement
# PR (W2-T7) lands, the `instance_*` outputs above will be reassigned to
# the v2 instance and these `_v2`-suffixed outputs will be removed; until
# then, both pairs of outputs coexist so the cutover script can construct
# both DSNs in parallel for verification (`pg_dump | psql` row-count
# checks).
# ---------------------------------------------------------------------------

output "instance_arn_v2" {
  description = "ARN of the v2 RDS PostgreSQL auth-db instance (snapshot-restored, encrypted under the consolidated panakoes/app-data CMK). After cutover this becomes the live auth-db; the v1 instance is retired in a follow-up PR."
  value       = aws_db_instance.auth_db_v2.arn
}

output "instance_endpoint_v2" {
  description = "Connection endpoint (hostname:port) for the v2 instance. The cutover script writes this into the `panakoes-dev/database-url` Secrets Manager value via `aws secretsmanager put-secret-value` (the secret carries lifecycle ignore_changes=[secret_string] so Terraform cannot drive the rotation directly)."
  value       = aws_db_instance.auth_db_v2.endpoint
}

output "instance_address_v2" {
  description = "DNS name (without port) of the v2 instance. Same hostname as `instance_endpoint_v2` minus the `:5432` suffix."
  value       = aws_db_instance.auth_db_v2.address
}

output "kms_key_arn_v2" {
  description = "ARN of the consolidated panakoes/app-data CMK encrypting the v2 instance's storage and Performance Insights data. Equal to the `app_data_key_arn` output of `infra/dev/kms/`."
  value       = local.app_data_kms_key_arn
}

output "pre_migration_snapshot_arn" {
  description = "ARN of the manual snapshot taken from the v1 instance before the W2-T5 re-encryption. Retained as a rollback artifact; orchestrator deletes it explicitly after burn-in (W2-T7 retirement)."
  value       = aws_db_snapshot.pre_migration.db_snapshot_arn
}

output "re_encrypted_snapshot_arn" {
  description = "ARN of the snapshot copy re-encrypted under the consolidated CMK; the v2 instance was restored from this snapshot. Retained for rollback parity with `pre_migration_snapshot_arn`."
  value       = aws_db_snapshot_copy.re_encrypted.db_snapshot_arn
}
