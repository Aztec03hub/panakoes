output "queue_arn" {
  description = "ARN of the SQS trigger queue. Use in IAM policies that need sqs:SendMessage to the queue (e.g. cross-module integrations)."
  value       = aws_sqs_queue.trigger.arn
}

output "queue_url" {
  description = "URL of the SQS trigger queue. Use in consumer SDKs (the worker Lambda's event-source mapping handles this internally)."
  value       = aws_sqs_queue.trigger.url
}

output "queue_name" {
  description = "Name of the SQS trigger queue."
  value       = aws_sqs_queue.trigger.name
}

output "dlq_arn" {
  description = "ARN of the dead-letter queue. Inspect manually when the trigger-dlq-not-empty alarm fires."
  value       = aws_sqs_queue.trigger_dlq.arn
}

output "dlq_name" {
  description = "Name of the dead-letter queue."
  value       = aws_sqs_queue.trigger_dlq.name
}

output "lambda_function_name" {
  description = "Name of the worker Lambda function."
  value       = aws_lambda_function.worker.function_name
}

output "lambda_function_arn" {
  description = "ARN of the worker Lambda function."
  value       = aws_lambda_function.worker.arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule on the default bus that fans S3 ObjectCreated events into the trigger queue."
  value       = aws_cloudwatch_event_rule.audio_uploaded.arn
}

output "log_group_name" {
  description = "CloudWatch log group the Lambda writes to."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "kms_key_arn" {
  description = "ARN of the dedicated CMK encrypting the trigger queue + DLQ."
  value       = aws_kms_key.queue.arn
}
