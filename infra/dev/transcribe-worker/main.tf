locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "transcribe-worker"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Wildcard ARN for the Groq API key Secrets Manager secret. The
  # secrets module owns the secret resource itself; this module only
  # references its ARN. The trailing six question marks match AWS's
  # auto-generated random suffix, so a recreate of the secret does not
  # invalidate this policy.
  groq_api_key_secret_arn = "arn:${local.partition}:secretsmanager:${local.region}:${local.account_id}:secret:${local.name_prefix}/groq-api-key-??????"

  # SQS visibility timeout: 5 minutes. Each message triggers one Groq
  # transcription + a DynamoDB write; both fit comfortably under this
  # budget. Lambda timeout matches so a slow invocation cannot exceed
  # SQS visibility (which would cause double-delivery).
  visibility_timeout_seconds = 300
  lambda_timeout_seconds     = 300

  # 3 receive attempts before DLQ. Lower than the events module's 5 because
  # transcription failures are usually terminal (auth, malformed key) and
  # `transcribe_ingestion` already converts most backend errors into
  # `transcript_status=failed` on first try; a second + third receive is
  # redundant for those, but useful for true transients (network blips,
  # cold-start timeouts).
  max_receive_count = 3
}

# ===========================================================================
# KMS CMK for the trigger queue
#
# Dedicated CMK rather than reusing the events module's shared key. The
# transcribe-worker module is a peer-deployable; it must not depend on
# the events module being applied (and may want different rotation /
# grants down the road). Cost: ~$1/mo. Mirrors the security pattern in
# `infra/dev/events/`.
# ===========================================================================

resource "aws_kms_key" "queue" {
  description             = "KMS key for the dev transcribe-worker SQS trigger queue"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountPermissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid    = "AllowAWSServicesToUseKey"
        Effect = "Allow"
        Principal = {
          Service = [
            "events.amazonaws.com",
            "sqs.amazonaws.com",
            "lambda.amazonaws.com",
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

resource "aws_kms_alias" "queue" {
  name          = "alias/${local.name_prefix}-transcribe-trigger"
  target_key_id = aws_kms_key.queue.key_id
}

# ===========================================================================
# SQS DLQ + live queue
#
# DLQ first because the live queue references its ARN. 14-day retention on
# both (the brief specifies 14d on the live queue; we mirror on the DLQ so
# operators get the same window to inspect failures before AWS expires them).
# ===========================================================================

resource "aws_sqs_queue" "trigger_dlq" {
  name                      = "${local.name_prefix}-transcribe-trigger-dlq"
  message_retention_seconds = 1209600 # 14 days
  kms_master_key_id         = aws_kms_key.queue.arn

  tags = local.common_tags
}

resource "aws_sqs_queue" "trigger" {
  name                       = "${local.name_prefix}-transcribe-trigger"
  visibility_timeout_seconds = local.visibility_timeout_seconds
  message_retention_seconds  = 1209600 # 14 days
  kms_master_key_id          = aws_kms_key.queue.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.trigger_dlq.arn
    maxReceiveCount     = local.max_receive_count
  })

  tags = local.common_tags
}

# EventBridge rules need permission to SendMessage to the trigger queue.
# Scoped to the exact rule ARN.
data "aws_iam_policy_document" "trigger_queue" {
  statement {
    sid    = "AllowEventBridgeSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.trigger.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.audio_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "trigger" {
  queue_url = aws_sqs_queue.trigger.id
  policy    = data.aws_iam_policy_document.trigger_queue.json
}

# ===========================================================================
# S3 -> EventBridge notification on the audio-uploads bucket
#
# IMPORTANT: S3 only allows ONE aws_s3_bucket_notification per bucket.
# infra/dev/storage/ does NOT declare one today (verified at module
# authorship), so this resource owns the notification config in full.
# If a future module needs to add another notification (e.g. a
# CloudFront-purge-on-delete Lambda), it must MERGE here, not declare a
# parallel resource (the second silently replaces the first).
# ===========================================================================

resource "aws_s3_bucket_notification" "audio_uploads" {
  bucket      = local.audio_uploads_bucket_name
  eventbridge = true
}

# ===========================================================================
# EventBridge rule on the default bus
#
# S3 emits "Object Created" to the default bus when EventBridge
# notifications are enabled on the bucket. We match that shape and
# narrow to (a) our bucket, (b) keys under the `audio/` prefix so
# accidental uploads outside that path do not trigger transcription.
# Target = the trigger SQS queue.
# ===========================================================================

resource "aws_cloudwatch_event_rule" "audio_uploaded" {
  name        = "${local.name_prefix}-transcribe-trigger"
  description = "S3 ObjectCreated events on the audio-uploads bucket under audio/* drive auto-transcription"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [local.audio_uploads_bucket_name]
      }
      object = {
        key = [{ prefix = "audio/" }]
      }
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "audio_uploaded" {
  rule      = aws_cloudwatch_event_rule.audio_uploaded.name
  target_id = "transcribe-trigger-queue"
  arn       = aws_sqs_queue.trigger.arn
}

# ===========================================================================
# IAM: scoped policy attached to the existing transcribe-worker task role
#
# The role itself lives in infra/dev/iam/ alongside every other service
# role (per the iam module convention; see the brief's "mirror the
# per-service-task-role pattern"). Here we attach the resource-tight
# policy that depends on resources this module owns (SQS, KMS) plus
# remote-state-fetched ARNs (ingestion table, audio bucket, secrets).
# ===========================================================================

data "aws_iam_policy_document" "worker_runtime" {
  # SQS: receive + delete + queue-attribute reads against the trigger queue only.
  statement {
    sid    = "ConsumeTranscribeTriggerQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.trigger.arn]
  }

  # S3: read audio uploads only, narrowed to the audio/ prefix.
  statement {
    sid       = "ReadAudioObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.audio_uploads_bucket_arn}/audio/*"]
  }

  # KMS: decrypt the audio bucket (so GetObject works on SSE-KMS) plus
  # the SQS queue's CMK (so ReceiveMessage works on the encrypted queue).
  statement {
    sid       = "DecryptAudioAndQueue"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [local.audio_uploads_kms_key_arn, aws_kms_key.queue.arn]
  }

  # DynamoDB: update the ingestion record's transcript fields.
  statement {
    sid       = "UpdateIngestionRecord"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem", "dynamodb:GetItem"]
    resources = [local.ingestion_table_arn]
  }

  # Secrets Manager: read the Groq API key.
  statement {
    sid       = "ReadGroqApiKey"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.groq_api_key_secret_arn]
  }
}

resource "aws_iam_role_policy" "worker_runtime" {
  name   = "${local.name_prefix}-transcribe-worker-runtime"
  role   = local.lambda_role_name
  policy = data.aws_iam_policy_document.worker_runtime.json
}

# ===========================================================================
# CloudWatch log group
#
# 30-day retention matches the platform default (CLAUDE.md). Encrypted
# with the shared logs CMK from the observability module so the rest of
# the log-archive pipeline (subscription filter -> S3) keeps working
# without per-group key fanout.
# ===========================================================================

# Dedicated CMK for the Lambda log group. The observability module's
# shared logs CMK conditions encryption on log-group ARNs matching
# `arn:aws:logs:us-east-1:659225405128:log-group:/panakoes/dev/*`;
# Lambda log groups land under `/aws/lambda/*`, which does not match
# that condition, so the shared key cannot encrypt them. Mirroring the
# pattern in `infra/dev/waf/` (which owns its own dedicated logs CMK
# for the same reason), we own a per-module CMK here scoped to this
# specific Lambda log group ARN.
data "aws_iam_policy_document" "log_kms" {
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning key (`aws_kms_key.log`).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  statement {
    sid    = "AllowCloudWatchLogsEncrypt"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; EncryptionContext
    # condition below pins use to this Lambda's log group ARN.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-transcribe-worker"]
    }
  }
}

resource "aws_kms_key" "log" {
  description             = "KMS key for the dev transcribe-worker Lambda log group"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.log_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "log" {
  name          = "alias/${local.name_prefix}-transcribe-worker-log"
  target_key_id = aws_kms_key.log.key_id
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-transcribe-worker"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.log.arn

  tags = local.common_tags
}

# ===========================================================================
# Lambda function
#
# Container image (built from services/transcribe-worker/Dockerfile,
# pushed to the panakoes-dev-transcribe-worker ECR repo). Until the
# image at var.lambda_image_tag exists in ECR the function will exist
# in TF state but error on every invocation; that is the documented
# operator follow-up after first apply.
# ===========================================================================

resource "aws_lambda_function" "worker" {
  function_name = "${local.name_prefix}-transcribe-worker"
  description   = "SQS-driven worker that auto-fires transcription on each S3 ObjectCreated event for the audio-uploads bucket. Reuses ingestion-api's transcribe_ingestion orchestration."

  package_type  = "Image"
  image_uri     = "${local.ecr_repo_url}:${var.lambda_image_tag}"
  role          = local.lambda_role_arn
  timeout       = local.lambda_timeout_seconds
  memory_size   = 512 # 100 MB upload cap + headroom
  architectures = ["x86_64"]

  # Reserved concurrency intentionally NULL in dev. AWS account-default
  # quota for `UnreservedConcurrentExecution` is 10 (not the docs-advertised
  # 1000); reserving any concurrency drops unreserved below the 10-floor
  # AWS enforces, and `PutFunctionConcurrency` rejects with
  # `InvalidParameterValueException: Specified ReservedConcurrentExecutions
  # for function decreases account's UnreservedConcurrentExecution below
  # its minimum value of [10].` Re-enable when the account's quota is
  # raised via Service Quotas (production target: 1000+).
  reserved_concurrent_executions = null

  environment {
    variables = {
      DDB_INGESTION_TABLE    = data.terraform_remote_state.data.outputs.ingestion_table_name
      AUDIO_UPLOADS_BUCKET   = local.audio_uploads_bucket_name
      DEPLOYMENT_ENVIRONMENT = var.environment
      TRANSCRIBER_BACKEND    = "groq"
      # GROQ_API_KEY is NOT injected here; the Lambda fetches the secret
      # at runtime via the Secrets Manager Lambda extension or an explicit
      # client call. Wiring the secret as a Lambda env var would commit it
      # to the function configuration's plaintext (visible to anyone with
      # lambda:GetFunction). The IAM policy above grants the read; the
      # operator-set secret value lands separately (Section D).
      #
      # Until the secret-extension wiring lands, an interim env-var injection
      # path is acceptable in dev: operator runs
      #   aws lambda update-function-configuration --environment "Variables={...,GROQ_API_KEY=<value>}"
      # after the first apply. Tracked as part of the Section D follow-up.
    }
  }

  tracing_config {
    mode = "Active" # X-Ray; matches the OTel cold-start hook in the worker
  }

  depends_on = [
    aws_iam_role_policy.worker_runtime,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = local.common_tags
}

# ===========================================================================
# SQS event-source mapping: SQS -> Lambda
#
# batch_size = 1 because each Groq call is independent and the per-message
# work is heavy enough that batching offers no throughput benefit; the
# `batchItemFailures` response shape lets the worker selectively re-queue
# a single failing record without disturbing the rest of a batch (matters
# more if batch_size goes up later).
# ===========================================================================

resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn = aws_sqs_queue.trigger.arn
  function_name    = aws_lambda_function.worker.arn

  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  enabled                            = true

  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy.worker_runtime,
  ]
}

# ===========================================================================
# CloudWatch alarm on the trigger DLQ
#
# Mirrors the alarm pattern from infra/dev/events/. Any visible message
# in the DLQ means at least one transcription attempt failed 3 receives;
# alert via the system-alerts SNS topic owned by the events module.
# ===========================================================================

data "terraform_remote_state" "events" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/events/terraform.tfstate"
    region = "us-east-1"
  }
}

resource "aws_cloudwatch_metric_alarm" "trigger_dlq" {
  alarm_name          = "${local.name_prefix}-transcribe-trigger-dlq-not-empty"
  alarm_description   = "Transcribe-trigger DLQ has at least one message; auto-transcription failed ${local.max_receive_count} receives"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.trigger_dlq.name
  }

  alarm_actions = [data.terraform_remote_state.events.outputs.sns_topic_arns["system-alerts"]]
  ok_actions    = [data.terraform_remote_state.events.outputs.sns_topic_arns["system-alerts"]]

  tags = local.common_tags
}
