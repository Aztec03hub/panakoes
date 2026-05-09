output "ingestion_table_name" {
  description = "Name of the panakoes-dev-ingestion DynamoDB table."
  value       = aws_dynamodb_table.ingestion.name
}

output "ingestion_table_arn" {
  description = "ARN of the panakoes-dev-ingestion DynamoDB table."
  value       = aws_dynamodb_table.ingestion.arn
}

output "audit_log_table_name" {
  description = "Name of the panakoes-dev-audit-log DynamoDB table."
  value       = aws_dynamodb_table.audit_log.name
}

output "audit_log_table_arn" {
  description = "ARN of the panakoes-dev-audit-log DynamoDB table."
  value       = aws_dynamodb_table.audit_log.arn
}

output "audit_log_tier3_action_index_arn" {
  description = "ARN of the Tier3ActionIndex GSI on the audit-log table. admin-api uses this to back the Tier 3.3 audit-log read view."
  value       = "${aws_dynamodb_table.audit_log.arn}/index/Tier3ActionIndex"
}

output "streaming_sessions_table_name" {
  description = "Name of the panakoes-dev-streaming-sessions DynamoDB table."
  value       = aws_dynamodb_table.streaming_sessions.name
}

output "streaming_sessions_table_arn" {
  description = "ARN of the panakoes-dev-streaming-sessions DynamoDB table."
  value       = aws_dynamodb_table.streaming_sessions.arn
}
