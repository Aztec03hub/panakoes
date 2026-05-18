output "app_data_key_arn" {
  description = "ARN of the consolidated app-data CMK. Consumed by storage, frontend, secrets, events, ecr, auth-db-rds modules in Wave 2 follow-up PRs."
  value       = aws_kms_key.app_data.arn
}

output "app_data_key_id" {
  description = "ID of the consolidated app-data CMK."
  value       = aws_kms_key.app_data.key_id
}

output "app_data_alias_name" {
  description = "Alias name (alias/panakoes/app-data) for the consolidated app-data CMK."
  value       = aws_kms_alias.app_data.name
}

output "logs_key_arn" {
  description = "ARN of the consolidated logs CMK. Consumed by observability and any module that creates a CloudWatch log group in Wave 2 follow-up PRs."
  value       = aws_kms_key.logs.arn
}

output "logs_key_id" {
  description = "ID of the consolidated logs CMK."
  value       = aws_kms_key.logs.key_id
}

output "logs_alias_name" {
  description = "Alias name (alias/panakoes/logs) for the consolidated logs CMK."
  value       = aws_kms_alias.logs.name
}
