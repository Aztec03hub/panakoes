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
  description = "ARN of the customer-managed KMS key encrypting the instance's storage and Performance Insights data. Required for any consumer IAM policy that needs `kms:Decrypt` on RDS-encrypted snapshots or PI data."
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
