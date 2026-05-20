output "pool_table_name" {
  description = "Name of the DDB table holding pool-claim state. The gpu-spawner reads/writes this table for drain-then-claim."
  value       = aws_dynamodb_table.frame_pool.name
}

output "pool_table_arn" {
  description = "ARN of the DDB pool-state table. Used by the gpu-spawner IAM policy to grant Scan + UpdateItem + GetItem."
  value       = aws_dynamodb_table.frame_pool.arn
}

output "pool_queue_arns" {
  description = "Map of pool slot id (string '0'..'N-1') to its SQS queue ARN. The gpu-spawner IAM policy grants sqs:ReceiveMessage + sqs:DeleteMessage* across this set."
  value       = { for k, q in aws_sqs_queue.pool : k => q.arn }
}

output "pool_queue_urls" {
  description = "Map of pool slot id (string '0'..'N-1') to its SQS queue URL. The DDB pool-state table is seeded with these URLs."
  value       = { for k, q in aws_sqs_queue.pool : k => q.id }
}

output "pool_size" {
  description = "Number of slots in the pool. Surface as an output so consumers (gpu-spawner config, dashboards) read the size from state without duplicating the variable."
  value       = var.pool_size
}
