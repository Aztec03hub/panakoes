output "cluster_arn" {
  description = "ARN of the Aurora Serverless v2 auth-db cluster. Required in any IAM policy that grants Aurora API actions (Describe, Modify, etc.) to a consuming role."
  value       = aws_rds_cluster.auth_db.arn
}

output "cluster_endpoint" {
  description = "Writer endpoint for the cluster. Use this for read-write traffic from the auth service. Resolves to the active writer instance under the hood; AWS handles failover transparently."
  value       = aws_rds_cluster.auth_db.endpoint
}

output "reader_endpoint" {
  description = "Reader endpoint for the cluster. With a single instance in dev this resolves to the writer; once a reader instance is added in prod or HA-dev, traffic distributes across readers automatically. Use for read-only workloads (analytics, reports)."
  value       = aws_rds_cluster.auth_db.reader_endpoint
}

output "port" {
  description = "TCP port the cluster listens on (5432 for Postgres). Surfaced as an output so the security group rule and the auth service connection string stay in sync without hardcoding the value in two places."
  value       = aws_rds_cluster.auth_db.port
}

output "security_group_id" {
  description = "ID of the cluster security group. Consumers (ECS task SGs, Lambda VPC SGs) reference this in their own egress rules; today inbound is open from the VPC CIDR, but a future tightening pass will replace the CIDR rule with security-group-to-security-group references and this output becomes the source of truth for that wiring."
  value       = aws_security_group.auth_db.id
}

output "kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting the cluster's storage and Performance Insights data. Required for any consumer IAM policy that needs `kms:Decrypt` on Aurora-encrypted snapshots or PI data."
  value       = aws_kms_key.auth_db.arn
}
