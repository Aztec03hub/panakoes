output "event_bus_arn" {
  description = "ARN of the Panakoes custom EventBridge bus. Services publishing domain events target this bus, not the default."
  value       = aws_cloudwatch_event_bus.panakoes.arn
}

output "event_bus_name" {
  description = "Name of the Panakoes custom EventBridge bus."
  value       = aws_cloudwatch_event_bus.panakoes.name
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting SNS topics and SQS queues. Required in IAM policies that grant kms:Decrypt or kms:GenerateDataKey to consumers. W2-T3: now returns the consolidated panakoes/app-data CMK ARN; the module-local aws_kms_key.events resource is retained for W2-T7 retirement but no longer encrypts new messages."
  value       = local.app_data_kms_key_arn
}

output "sns_topic_arns" {
  description = "Map of SNS topic name to ARN. Keys: system-alerts, billing-events, user-notifications."
  value = {
    system-alerts      = aws_sns_topic.system_alerts.arn
    billing-events     = aws_sns_topic.billing_events.arn
    user-notifications = aws_sns_topic.user_notifications.arn
  }
}

output "sqs_queue_urls" {
  description = "Map of SQS queue logical name to queue URL (used by consumer SDKs to poll). Includes live queues and DLQs."
  value = {
    audio-uploaded-queue       = aws_sqs_queue.audio_uploaded.url
    audio-uploaded-dlq         = aws_sqs_queue.audio_uploaded_dlq.url
    transcript-completed-queue = aws_sqs_queue.transcript_completed.url
    transcript-completed-dlq   = aws_sqs_queue.transcript_completed_dlq.url
    summary-completed-queue    = aws_sqs_queue.summary_completed.url
    summary-completed-dlq      = aws_sqs_queue.summary_completed_dlq.url
    notification-queue         = aws_sqs_queue.notification.url
    notification-dlq           = aws_sqs_queue.notification_dlq.url
  }
}

output "sqs_queue_arns" {
  description = "Map of SQS queue logical name to ARN. Use in IAM policies that grant sqs:SendMessage / sqs:ReceiveMessage."
  value = {
    audio-uploaded-queue       = aws_sqs_queue.audio_uploaded.arn
    audio-uploaded-dlq         = aws_sqs_queue.audio_uploaded_dlq.arn
    transcript-completed-queue = aws_sqs_queue.transcript_completed.arn
    transcript-completed-dlq   = aws_sqs_queue.transcript_completed_dlq.arn
    summary-completed-queue    = aws_sqs_queue.summary_completed.arn
    summary-completed-dlq      = aws_sqs_queue.summary_completed_dlq.arn
    notification-queue         = aws_sqs_queue.notification.arn
    notification-dlq           = aws_sqs_queue.notification_dlq.arn
  }
}
