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

output "streaming_sessions_table_name" {
  description = "Name of the panakoes-dev-streaming-sessions DynamoDB table."
  value       = aws_dynamodb_table.streaming_sessions.name
}

output "streaming_sessions_table_arn" {
  description = "ARN of the panakoes-dev-streaming-sessions DynamoDB table."
  value       = aws_dynamodb_table.streaming_sessions.arn
}
