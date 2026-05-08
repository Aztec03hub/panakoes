locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "events"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  audio_uploads_bucket_name = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_name
  audio_uploads_bucket_arn  = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_arn
}

# ===========================================================================
# KMS CMK for SNS + SQS
#
# Single CMK shared across SNS topics and SQS queues in this module.
# Sharing one key (vs one per topic/queue) keeps fixed cost low ($1/mo
# instead of N x $1/mo) and is appropriate because every consumer of
# these messages is the same trust boundary (Panakoes services).
#
# 7-day deletion window matches the events module's lower-stakes
# data (transient pipeline messages, not durable storage).
# ===========================================================================

resource "aws_kms_key" "events" {
  description             = "KMS key for the dev events SNS topics and SQS queues"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  # Allow the local AWS account root to administer the key, plus
  # explicitly allow AWS services (SNS, SQS, EventBridge, CloudWatch
  # Events, S3) that need to use it for encrypt/decrypt operations
  # when delivering messages on behalf of Panakoes resources.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountPermissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${local.partition}:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowAWSServicesToUseKey"
        Effect = "Allow"
        Principal = {
          Service = [
            "events.amazonaws.com",
            "sns.amazonaws.com",
            "sqs.amazonaws.com",
            "s3.amazonaws.com",
            "cloudwatch.amazonaws.com",
          ]
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "events" {
  name          = "alias/${local.name_prefix}-events"
  target_key_id = aws_kms_key.events.key_id
}

# ===========================================================================
# EventBridge custom bus
#
# Custom bus separates Panakoes domain events from the AWS default
# bus. Keeping our events on a dedicated bus means rules and metrics
# scope cleanly, and a misconfigured rule on the default bus cannot
# accidentally pick up our pipeline traffic.
# ===========================================================================

resource "aws_cloudwatch_event_bus" "panakoes" {
  name = local.name_prefix
  tags = local.common_tags
}

# ===========================================================================
# SQS DLQs (declared first because the live queues reference their ARNs
# in the redrive policy)
#
# Each pipeline stage gets its own DLQ. Messages land here after 5
# unsuccessful receives on the live queue. Retention matches the live
# queues (4 days) so engineers have a window to inspect failures
# before AWS expires them.
# ===========================================================================

resource "aws_sqs_queue" "audio_uploaded_dlq" {
  name                      = "${local.name_prefix}-audio-uploaded-dlq"
  message_retention_seconds = 345600 # 4 days
  kms_master_key_id         = aws_kms_key.events.arn

  tags = local.common_tags
}

resource "aws_sqs_queue" "transcript_completed_dlq" {
  name                      = "${local.name_prefix}-transcript-completed-dlq"
  message_retention_seconds = 345600
  kms_master_key_id         = aws_kms_key.events.arn

  tags = local.common_tags
}

resource "aws_sqs_queue" "summary_completed_dlq" {
  name                      = "${local.name_prefix}-summary-completed-dlq"
  message_retention_seconds = 345600
  kms_master_key_id         = aws_kms_key.events.arn

  tags = local.common_tags
}

resource "aws_sqs_queue" "notification_dlq" {
  name                      = "${local.name_prefix}-notification-dlq"
  message_retention_seconds = 345600
  kms_master_key_id         = aws_kms_key.events.arn

  tags = local.common_tags
}

# ===========================================================================
# SQS live queues
#
# Each consumer (a Lambda or container) polls one of these. Visibility
# timeout 60s gives consumers a full minute to process a message
# before SQS makes it visible again (long enough for transcription
# webhooks but short enough that crashed consumers redeliver fast).
# Redrive policy points at the matching DLQ after 5 receive attempts.
# ===========================================================================

resource "aws_sqs_queue" "audio_uploaded" {
  name                       = "${local.name_prefix}-audio-uploaded-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600 # 4 days
  kms_master_key_id          = aws_kms_key.events.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.audio_uploaded_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

resource "aws_sqs_queue" "transcript_completed" {
  name                       = "${local.name_prefix}-transcript-completed-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  kms_master_key_id          = aws_kms_key.events.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.transcript_completed_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

resource "aws_sqs_queue" "summary_completed" {
  name                       = "${local.name_prefix}-summary-completed-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  kms_master_key_id          = aws_kms_key.events.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.summary_completed_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

resource "aws_sqs_queue" "notification" {
  name                       = "${local.name_prefix}-notification-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  kms_master_key_id          = aws_kms_key.events.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notification_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

# ===========================================================================
# SQS queue policies
#
# EventBridge rules need permission to SendMessage to their target
# queues. Each policy scopes the allow to the exact rule ARN that
# targets that queue (plus the SNS topic for the notification queue).
# ===========================================================================

data "aws_iam_policy_document" "audio_uploaded_queue" {
  statement {
    sid    = "AllowEventBridgeSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.audio_uploaded.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.audio_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "audio_uploaded" {
  queue_url = aws_sqs_queue.audio_uploaded.id
  policy    = data.aws_iam_policy_document.audio_uploaded_queue.json
}

data "aws_iam_policy_document" "transcript_completed_queue" {
  statement {
    sid    = "AllowEventBridgeSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.transcript_completed.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.transcript_completed.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "transcript_completed" {
  queue_url = aws_sqs_queue.transcript_completed.id
  policy    = data.aws_iam_policy_document.transcript_completed_queue.json
}

data "aws_iam_policy_document" "summary_completed_queue" {
  statement {
    sid    = "AllowEventBridgeSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.summary_completed.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.summary_completed.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "summary_completed" {
  queue_url = aws_sqs_queue.summary_completed.id
  policy    = data.aws_iam_policy_document.summary_completed_queue.json
}

data "aws_iam_policy_document" "notification_queue" {
  statement {
    sid    = "AllowSNSSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.notification.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.user_notifications.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "notification" {
  queue_url = aws_sqs_queue.notification.id
  policy    = data.aws_iam_policy_document.notification_queue.json
}

# ===========================================================================
# EventBridge rules
#
# Each rule selects events on the custom Panakoes bus and routes them
# to the matching SQS queue. EventBridge does not retry into SQS the
# way it does into Lambda; redelivery is the queue consumer's
# responsibility, and unprocessable messages flow to the DLQ via the
# queue's redrive policy.
# ===========================================================================

resource "aws_cloudwatch_event_rule" "audio_uploaded" {
  name           = "${local.name_prefix}-audio-uploaded"
  description    = "S3 ObjectCreated events on the audio-uploads bucket"
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name

  # S3 emits "Object Created" events to the default bus when EventBridge
  # notifications are enabled on the bucket. We mirror those events
  # onto the Panakoes bus via a separate config (deferred to the
  # storage module update); this rule matches the same shape so the
  # transition is transparent. Filter narrows the match to our bucket.
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [local.audio_uploads_bucket_name]
      }
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "audio_uploaded" {
  rule           = aws_cloudwatch_event_rule.audio_uploaded.name
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name
  target_id      = "audio-uploaded-queue"
  arn            = aws_sqs_queue.audio_uploaded.arn
}

resource "aws_cloudwatch_event_rule" "transcript_completed" {
  name           = "${local.name_prefix}-transcript-completed"
  description    = "Custom event emitted when the transcribe service finishes a job"
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name

  event_pattern = jsonencode({
    source      = ["panakoes.transcribe"]
    detail-type = ["TranscriptCompleted"]
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "transcript_completed" {
  rule           = aws_cloudwatch_event_rule.transcript_completed.name
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name
  target_id      = "transcript-completed-queue"
  arn            = aws_sqs_queue.transcript_completed.arn
}

resource "aws_cloudwatch_event_rule" "summary_completed" {
  name           = "${local.name_prefix}-summary-completed"
  description    = "Custom event emitted when the summarize service finishes a job"
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name

  event_pattern = jsonencode({
    source      = ["panakoes.summarize"]
    detail-type = ["SummaryCompleted"]
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "summary_completed" {
  rule           = aws_cloudwatch_event_rule.summary_completed.name
  event_bus_name = aws_cloudwatch_event_bus.panakoes.name
  target_id      = "summary-completed-queue"
  arn            = aws_sqs_queue.summary_completed.arn
}

# ===========================================================================
# SNS topics
#
# Three fan-out topics that the rest of the platform publishes to:
#   - system-alerts: ops alerting (DLQ alarms publish here, plus any
#     future runtime alarms).
#   - billing-events: webhook fan-out for Stripe billing events; lets
#     multiple subscribers (audit log writer, customer email, account
#     state machine) react in parallel.
#   - user-notifications: end-user notifications (email/push); the
#     notification queue subscribes to drive the actual delivery
#     workers.
#
# Topic policies allow only same-account principals to publish, scoped
# via the aws:SourceAccount condition. This rejects cross-account
# publish attempts while still permitting in-account services and
# CloudWatch alarms to send messages.
# ===========================================================================

resource "aws_sns_topic" "system_alerts" {
  name              = "${local.name_prefix}-system-alerts"
  kms_master_key_id = aws_kms_key.events.arn

  tags = local.common_tags
}

resource "aws_sns_topic" "billing_events" {
  name              = "${local.name_prefix}-billing-events"
  kms_master_key_id = aws_kms_key.events.arn

  tags = local.common_tags
}

resource "aws_sns_topic" "user_notifications" {
  name              = "${local.name_prefix}-user-notifications"
  kms_master_key_id = aws_kms_key.events.arn

  tags = local.common_tags
}

data "aws_iam_policy_document" "system_alerts" {
  statement {
    sid    = "AllowSameAccountPublish"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.system_alerts.arn]
  }

  statement {
    sid    = "AllowCloudWatchAlarmsPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.system_alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "system_alerts" {
  arn    = aws_sns_topic.system_alerts.arn
  policy = data.aws_iam_policy_document.system_alerts.json
}

data "aws_iam_policy_document" "billing_events" {
  statement {
    sid    = "AllowSameAccountPublish"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.billing_events.arn]
  }
}

resource "aws_sns_topic_policy" "billing_events" {
  arn    = aws_sns_topic.billing_events.arn
  policy = data.aws_iam_policy_document.billing_events.json
}

data "aws_iam_policy_document" "user_notifications" {
  statement {
    sid    = "AllowSameAccountPublish"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.user_notifications.arn]
  }
}

resource "aws_sns_topic_policy" "user_notifications" {
  arn    = aws_sns_topic.user_notifications.arn
  policy = data.aws_iam_policy_document.user_notifications.json
}

# ===========================================================================
# SNS to SQS subscription: user-notifications -> notification queue
#
# Raw delivery so the queue receives the original message body without
# the SNS envelope, which keeps consumer code simpler.
# ===========================================================================

resource "aws_sns_topic_subscription" "user_notifications_to_queue" {
  topic_arn            = aws_sns_topic.user_notifications.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.notification.arn
  raw_message_delivery = true
}

# ===========================================================================
# CloudWatch alarms on each DLQ
#
# Any visible message in a DLQ means at least one pipeline message
# failed processing 5 times. We treat that as alert-worthy and publish
# to the system-alerts SNS topic. Threshold > 0 over 5 minutes
# (a single 5-minute period; alarm fires as soon as a message lingers).
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "audio_uploaded_dlq" {
  alarm_name          = "${local.name_prefix}-audio-uploaded-dlq-not-empty"
  alarm_description   = "Audio-uploaded DLQ has at least one message; pipeline message failed 5 receives"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.audio_uploaded_dlq.name
  }

  alarm_actions = [aws_sns_topic.system_alerts.arn]
  ok_actions    = [aws_sns_topic.system_alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "transcript_completed_dlq" {
  alarm_name          = "${local.name_prefix}-transcript-completed-dlq-not-empty"
  alarm_description   = "Transcript-completed DLQ has at least one message; pipeline message failed 5 receives"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.transcript_completed_dlq.name
  }

  alarm_actions = [aws_sns_topic.system_alerts.arn]
  ok_actions    = [aws_sns_topic.system_alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "summary_completed_dlq" {
  alarm_name          = "${local.name_prefix}-summary-completed-dlq-not-empty"
  alarm_description   = "Summary-completed DLQ has at least one message; pipeline message failed 5 receives"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.summary_completed_dlq.name
  }

  alarm_actions = [aws_sns_topic.system_alerts.arn]
  ok_actions    = [aws_sns_topic.system_alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "notification_dlq" {
  alarm_name          = "${local.name_prefix}-notification-dlq-not-empty"
  alarm_description   = "Notification DLQ has at least one message; user notification failed 5 receives"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.notification_dlq.name
  }

  alarm_actions = [aws_sns_topic.system_alerts.arn]
  ok_actions    = [aws_sns_topic.system_alerts.arn]

  tags = local.common_tags
}
