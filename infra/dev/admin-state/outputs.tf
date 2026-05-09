output "cost_cache_table_name" {
  description = "Name of the panakoes-dev-cost-cache DynamoDB table."
  value       = aws_dynamodb_table.cost_cache.name
}

output "cost_cache_table_arn" {
  description = "ARN of the panakoes-dev-cost-cache DynamoDB table."
  value       = aws_dynamodb_table.cost_cache.arn
}

output "tenant_cost_rollup_table_name" {
  description = "Name of the panakoes-dev-tenant-cost-rollup DynamoDB table."
  value       = aws_dynamodb_table.tenant_cost_rollup.name
}

output "tenant_cost_rollup_table_arn" {
  description = "ARN of the panakoes-dev-tenant-cost-rollup DynamoDB table."
  value       = aws_dynamodb_table.tenant_cost_rollup.arn
}

output "lifecycle_state_table_name" {
  description = "Name of the panakoes-dev-lifecycle-state DynamoDB table."
  value       = aws_dynamodb_table.lifecycle_state.name
}

output "lifecycle_state_table_arn" {
  description = "ARN of the panakoes-dev-lifecycle-state DynamoDB table."
  value       = aws_dynamodb_table.lifecycle_state.arn
}

output "alert_state_table_name" {
  description = "Name of the panakoes-dev-alert-state DynamoDB table."
  value       = aws_dynamodb_table.alert_state.name
}

output "alert_state_table_arn" {
  description = "ARN of the panakoes-dev-alert-state DynamoDB table."
  value       = aws_dynamodb_table.alert_state.arn
}
