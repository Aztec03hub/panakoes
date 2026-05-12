locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "step-functions"
  }

  name_prefix         = "${var.project_name}-${var.environment}"
  state_machine_name  = "${local.name_prefix}-long-audio"
  state_machine_role  = "${local.name_prefix}-long-audio-sfn-role"
  log_group_name      = "/aws/states/${local.state_machine_name}"
  account_id          = data.aws_caller_identity.current.account_id
  region              = data.aws_region.current.region
  arn_lambda_prefix   = "arn:aws:lambda:${local.region}:${local.account_id}:function"
  arn_batch_prefix    = "arn:aws:batch:${local.region}:${local.account_id}"
  arn_eventbridge_bus = "arn:aws:events:${local.region}:${local.account_id}:event-bus/${local.name_prefix}"

  # ---------------------------------------------------------------------------
  # Lambda ARN locals (forward references)
  #
  # None of these Lambdas exist yet. Each function name follows the
  # `panakoes-dev-<role>` pattern the rest of the project uses, so the
  # ARNs resolve cleanly when the deploy pipeline creates the functions
  # under those names. Documenting the contract up front means the
  # state-machine definition stays stable across the slices that ship
  # the Lambdas.
  #
  # Inputs and outputs each Lambda is expected to honor:
  #
  #   * detect_duration:
  #       in  = { ingestion_id, user_id, audio_s3_uri }
  #       out = { ingestion_id, user_id, audio_s3_uri,
  #               duration_seconds }
  #
  #   * chunk_audio:
  #       in  = { ingestion_id, audio_s3_uri,
  #               chunk_duration_seconds, chunk_overlap_seconds }
  #       out = { ingestion_id, chunks: [ { index, s3_uri,
  #               start_seconds, end_seconds } ] }
  #
  #   * merge_transcripts:
  #       in  = { ingestion_id, chunk_results: [...] }
  #       out = { ingestion_id, merged_transcript_s3_uri }
  #
  #   * write_final_transcript:
  #       in  = { ingestion_id, user_id,
  #               merged_transcript_s3_uri | single_transcript_s3_uri }
  #       out = { ingestion_id, final_transcript_s3_uri,
  #               status: "transcribed" }
  #
  #   * notify_failure:
  #       in  = { ingestion_id, user_id, error: { Error, Cause } }
  #       out = { ingestion_id, status: "failed" }
  # ---------------------------------------------------------------------------
  lambda_arns = {
    detect_duration        = "${local.arn_lambda_prefix}:${local.name_prefix}-detect-duration"
    chunk_audio            = "${local.arn_lambda_prefix}:${local.name_prefix}-chunk-audio"
    merge_transcripts      = "${local.arn_lambda_prefix}:${local.name_prefix}-merge-transcripts"
    write_final_transcript = "${local.arn_lambda_prefix}:${local.name_prefix}-write-final-transcript"
    notify_failure         = "${local.arn_lambda_prefix}:${local.name_prefix}-notify-failure"
  }

  # Lambda ARNs as a flat list, used when an IAM statement spans every
  # Lambda the state machine invokes.
  lambda_arn_list = values(local.lambda_arns)

  # ---------------------------------------------------------------------------
  # Batch job queue + definition locals (forward references via try())
  #
  # When `infra/dev/batch/` lands, its outputs replace the constructed
  # ARNs. Until then, we fall back to the documented names. Note: the
  # Batch SubmitJob API accepts both ARNs and names for JobQueue and
  # JobDefinition, but the IAM policy wants ARNs.
  # ---------------------------------------------------------------------------
  batch_job_queue_arn = try(
    data.terraform_remote_state.batch.outputs.transcribe_job_queue_arn,
    "${local.arn_batch_prefix}:job-queue/${local.name_prefix}-transcribe-batch",
  )

  batch_job_definition_arn = try(
    data.terraform_remote_state.batch.outputs.transcribe_job_definition_arn,
    "${local.arn_batch_prefix}:job-definition/${local.name_prefix}-transcribe-batch",
  )

  batch_job_queue_name = try(
    data.terraform_remote_state.batch.outputs.transcribe_job_queue_name,
    "${local.name_prefix}-transcribe-batch",
  )

  batch_job_definition_name = try(
    data.terraform_remote_state.batch.outputs.transcribe_job_definition_name,
    "${local.name_prefix}-transcribe-batch",
  )

  # ---------------------------------------------------------------------------
  # KMS key resolution
  #
  # If `infra/dev/observability/` is applied, use the shared dev
  # CloudWatch CMK from its outputs. Otherwise fall back to a CMK
  # provisioned in this module so the log group still encrypts under a
  # customer-managed key from day one. `try()` against the missing
  # remote state output triggers the fallback branch cleanly.
  # ---------------------------------------------------------------------------
  observability_kms_key_arn = try(
    data.terraform_remote_state.observability.outputs.cloudwatch_logs_kms_key_arn,
    null,
  )
  use_fallback_kms = local.observability_kms_key_arn == null
}

# ---------------------------------------------------------------------------
# Fallback KMS key for the state machine's CloudWatch log group
#
# Only created when `infra/dev/observability/` is not yet applied. Once
# observability lands, this key can be retired by deleting it from the
# module after `aws_cloudwatch_log_group.state_machine.kms_key_id` is
# pointed at the shared CMK. Rotation enabled, 7-day deletion window
# matches every other dev CMK in the project.
#
# CloudWatch Logs uses the documented service-principal-with-context
# delegation, scoped to this single log group's ARN to prevent
# unrelated log groups in the region from piggybacking on the key.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "fallback_log_kms" {
  count = local.use_fallback_kms ? 1 : 0

  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` refers to the owning fallback log key only.
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
    # condition pins use to this state machine's log group ARN.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:${local.log_group_name}"]
    }
  }
}

resource "aws_kms_key" "fallback_log" {
  count = local.use_fallback_kms ? 1 : 0

  description             = "Fallback CMK for the long-audio Step Functions log group. Replace with the shared observability CMK once `infra/dev/observability/` is applied."
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.fallback_log_kms[0].json

  tags = local.common_tags
}

resource "aws_kms_alias" "fallback_log" {
  count = local.use_fallback_kms ? 1 : 0

  name          = "alias/${local.name_prefix}-long-audio-sfn-logs"
  target_key_id = aws_kms_key.fallback_log[0].key_id
}

locals {
  log_group_kms_arn = local.use_fallback_kms ? aws_kms_key.fallback_log[0].arn : local.observability_kms_key_arn
}

# ---------------------------------------------------------------------------
# CloudWatch log group for the state machine
#
# Step Functions Standard workflows ship execution-history events to a
# log group when logging is configured. Level `ALL` plus
# `include_execution_data = true` is verbose, but the verbosity is
# load-bearing for postmortem analysis: it captures every state's input
# and output payload so a failed long-audio run can be replayed
# offline. Logs are KMS-encrypted; retention 30 days mirrors the
# project default.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "state_machine" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  kms_key_id        = local.log_group_kms_arn

  tags = local.common_tags
}

# ===========================================================================
# IAM role for the state machine
#
# The role is the runtime identity the Step Functions service assumes
# when executing the state machine. Its policy grants:
#
#   1. lambda:InvokeFunction on each Lambda named in `local.lambda_arns`
#      (no wildcards on the function ARN; the role can call those five
#      functions and nothing else).
#   2. batch:SubmitJob, batch:DescribeJobs, batch:TerminateJob on the
#      transcribe-batch job queue + definition. The Map state uses the
#      `.sync` integration which polls until the job finishes, so
#      DescribeJobs must be permitted; the integration also calls
#      TerminateJob on a Map abort, so we list it here too.
#   3. logs:CreateLogDelivery and friends on `*` because that is the
#      AWS-documented permission set for `aws_sfn_state_machine`'s
#      logging configuration. Resource = "*" is forced by the
#      service-side authorization model; we accept it for the logs
#      family only.
#   4. EventBridge PutEvents on the project bus for the failure-path
#      Lambda (which the orchestrator will call directly, but having
#      the state machine able to fire fault events from a Pass state in
#      a follow-up is cheap to grant up front).
# ===========================================================================

data "aws_iam_policy_document" "state_machine_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name                 = local.state_machine_role
  description          = "Runtime IAM role assumed by the long-audio Step Functions state machine in dev."
  assume_role_policy   = data.aws_iam_policy_document.state_machine_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid       = "InvokePipelineLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = local.lambda_arn_list
  }

  statement {
    sid    = "SubmitTranscribeBatchJobs"
    effect = "Allow"
    actions = [
      "batch:SubmitJob",
      "batch:TerminateJob",
    ]
    resources = [
      local.batch_job_queue_arn,
      local.batch_job_definition_arn,
    ]
  }

  # Batch DescribeJobs has no resource-level authorization, mirroring
  # ec2:DescribeInstances. The `.sync` Map integration polls Batch on
  # the customer's behalf and requires this verb. Tightened by the
  # service condition below would not help: there are no condition keys
  # exposed for DescribeJobs.
  statement {
    sid     = "DescribeBatchJobs"
    effect  = "Allow"
    actions = ["batch:DescribeJobs"]
    # panakoes-iam-policy-resource-star: justified
    # `batch:DescribeJobs` does not support resource-level authorization per
    # the AWS service-authorization reference; only `*` is valid. The `.sync`
    # Step Functions integration requires this verb to poll job state.
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_awsbatch.html
    resources = ["*"]
  }

  # Step Functions Standard logging requires the state machine's role
  # to be able to manage CloudWatch Logs delivery resources. AWS
  # documents `Resource = "*"` as required for these verbs; the verbs
  # themselves are scoped to log-delivery management, not arbitrary
  # logs reading.
  statement {
    sid    = "ManageLogDelivery"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    # panakoes-iam-policy-resource-star: justified
    # `logs:*LogDelivery`, `logs:PutResourcePolicy`,
    # `logs:DescribeResourcePolicies`, and `logs:DescribeLogGroups` do not
    # support resource-level authorization per the AWS service-authorization
    # reference; `*` is the only valid resource. These verbs are required
    # by Step Functions Standard logging configuration.
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatchlogs.html
    resources = ["*"]
  }

  # Direct writes into the state machine's own log group. The verbs
  # above are for the LogDelivery abstraction; once a delivery is
  # established the service still puts log events under its own
  # identity, so the role needs PutLogEvents scoped to this group.
  statement {
    sid    = "WriteOwnLogStream"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.state_machine.arn}:*",
    ]
  }

  # KMS rights for the log-group CMK so the state machine can encrypt
  # the log events it ships. Scoped to the single key the log group
  # uses; falls through to the observability key when that is wired in.
  statement {
    sid    = "EncryptOwnLogs"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [local.log_group_kms_arn]
  }

  # EventBridge PutEvents on the project bus so the state machine can
  # emit fault events directly in a follow-up (today the
  # `notify_failure` Lambda owns the PutEvents call, but having the
  # surface available is cheap and avoids a round-trip role bump
  # later).
  statement {
    sid       = "PutEventsOnProjectBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = [local.arn_eventbridge_bus]
  }

  # Step Functions managed-rule plumbing. The Map `.sync` integration
  # under the hood creates an EventBridge rule per execution that
  # listens for the Batch job-state-change event and feeds it back into
  # the state machine. AWS docs require these rights on the role.
  statement {
    sid    = "ManagedRuleForBatchSync"
    effect = "Allow"
    actions = [
      "events:PutTargets",
      "events:PutRule",
      "events:DescribeRule",
    ]
    resources = [
      "arn:aws:events:${local.region}:${local.account_id}:rule/StepFunctionsGetEventsForBatchJobsRule",
    ]
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "${local.state_machine_role}-policy"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

# ===========================================================================
# State machine definition
#
# The definition is built as an HCL local map and rendered with
# jsonencode(). Authoring it as HCL keeps the structure reviewable
# inline (no second-language quoting hell) and lets Terraform's plan
# output diff individual states cleanly when we tweak retry policies
# or routing.
#
# Workflow type = STANDARD. Standard executions run up to 365 days,
# emit a full audit trail, and bill per state transition. Express
# would be cheaper but caps at 5 minutes per execution, which would
# break any chunked transcription whose total wall-clock exceeds five
# minutes (which is most of them).
# ===========================================================================

locals {
  retry_policy = {
    ErrorEquals = [
      "Lambda.ServiceException",
      "Lambda.AWSLambdaException",
      "Lambda.SdkClientException",
      "Lambda.TooManyRequestsException",
      "States.TaskFailed",
      "States.Timeout",
    ]
    IntervalSeconds = var.task_retry_interval_seconds
    MaxAttempts     = var.task_retry_max_attempts
    BackoffRate     = 2.0
  }

  catch_to_failure = [{
    ErrorEquals = ["States.ALL"]
    Next        = "NotifyFailure"
    ResultPath  = "$.error"
  }]

  state_machine_definition = {
    Comment = "Long-audio transcription orchestration. Detects audio duration, branches between a single-Batch-job short path and a chunk-and-fan-out long path, merges chunked transcripts, writes the final transcript, and updates the ingestion record."
    StartAt = "DetectDuration"

    States = {

      # -------------------------------------------------------------------
      # 1. DetectDuration
      #
      # Probes the S3 object for its audio duration. The Lambda runs
      # a streaming ffprobe-equivalent against a range-read of the
      # file header so it does not need to download the whole audio.
      # Outputs `duration_seconds` alongside the original payload so
      # downstream states can read it.
      # -------------------------------------------------------------------
      DetectDuration = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.detect_duration
          "Payload.$"  = "$"
        }
        ResultSelector = {
          "duration_seconds.$" = "$.Payload.duration_seconds"
          "audio_s3_uri.$"     = "$.Payload.audio_s3_uri"
          "ingestion_id.$"     = "$.Payload.ingestion_id"
          "user_id.$"          = "$.Payload.user_id"
        }
        ResultPath = "$"
        Next       = "DurationChoice"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 2. DurationChoice
      #
      # Branches based on duration. Strict less-than against the
      # threshold sends the short-audio path; everything else (>=)
      # goes to the chunk path. The default is the long path so a
      # missing or zero duration field still produces a deterministic
      # routing (handled by the long path's chunk Lambda which is
      # already robust to short inputs).
      # -------------------------------------------------------------------
      DurationChoice = {
        Type = "Choice"
        Choices = [{
          Variable        = "$.duration_seconds"
          NumericLessThan = var.long_audio_threshold_seconds
          Next            = "SubmitBatchSingle"
        }]
        Default = "ChunkAudio"
      }

      # -------------------------------------------------------------------
      # 3. ChunkAudio
      #
      # Splits the audio into overlapping chunks. Output shape:
      # `chunks: [ { index, s3_uri, start_seconds, end_seconds } ]`.
      # The Lambda writes each chunk back to the audio-uploads bucket
      # under `audio/<user>/<ingestion>/chunks/<index>.<ext>` so the
      # transcribe-batch job can pick them up via the same access
      # pattern it uses for unchunked audio.
      # -------------------------------------------------------------------
      ChunkAudio = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.chunk_audio
          Payload = {
            "ingestion_id.$"       = "$.ingestion_id"
            "user_id.$"            = "$.user_id"
            "audio_s3_uri.$"       = "$.audio_s3_uri"
            "duration_seconds.$"   = "$.duration_seconds"
            chunk_duration_seconds = var.chunk_duration_seconds
            chunk_overlap_seconds  = var.chunk_overlap_seconds
          }
        }
        ResultSelector = {
          "chunks.$"       = "$.Payload.chunks"
          "ingestion_id.$" = "$.Payload.ingestion_id"
          "user_id.$"      = "$.Payload.user_id"
        }
        ResultPath = "$.chunking"
        Next       = "TranscribeChunksMap"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 4. TranscribeChunksMap
      #
      # Parallel fan-out over the chunk list. Each iteration submits a
      # `panakoes-dev-transcribe-batch` AWS Batch job and waits for
      # completion via the `.sync` integration. MaxConcurrency caps
      # the in-flight job count so a single huge audio file cannot
      # monopolize the GPU job queue and starve other tenants.
      #
      # JobName is templated per-chunk so CloudWatch Logs separates
      # the Batch job logs cleanly when we go hunting for a specific
      # chunk's transcription output.
      # -------------------------------------------------------------------
      TranscribeChunksMap = {
        Type           = "Map"
        ItemsPath      = "$.chunking.chunks"
        MaxConcurrency = var.map_max_concurrency
        ItemSelector = {
          "ingestion_id.$" = "$.ingestion_id"
          "user_id.$"      = "$.user_id"
          "chunk.$"        = "$$.Map.Item.Value"
        }
        ItemProcessor = {
          # Mode = INLINE runs each chunk's processor in-process within
          # the parent state machine; no `ExecutionType` field is allowed
          # in this mode (the Step Functions ASL validator rejects it
          # with `Field 'ExecutionType' is not supported`). ExecutionType
          # is only valid when Mode = "DISTRIBUTED" (Distributed Map),
          # which would spawn a child execution per chunk; we want
          # in-process for the chunking fan-out.
          ProcessorConfig = {
            Mode = "INLINE"
          }
          StartAt = "SubmitChunkBatchJob"
          States = {
            SubmitChunkBatchJob = {
              Type     = "Task"
              Resource = "arn:aws:states:::batch:submitJob.sync"
              Parameters = {
                "JobName.$"   = "States.Format('${local.batch_job_definition_name}-{}-{}', $.ingestion_id, $.chunk.index)"
                JobQueue      = local.batch_job_queue_name
                JobDefinition = local.batch_job_definition_name
                ContainerOverrides = {
                  Environment = [
                    {
                      Name      = "INGESTION_ID"
                      "Value.$" = "$.ingestion_id"
                    },
                    {
                      Name      = "CHUNK_INDEX"
                      "Value.$" = "States.JsonToString($.chunk.index)"
                    },
                    {
                      Name      = "CHUNK_S3_URI"
                      "Value.$" = "$.chunk.s3_uri"
                    },
                    {
                      Name      = "CHUNK_START_SECONDS"
                      "Value.$" = "States.JsonToString($.chunk.start_seconds)"
                    },
                    {
                      Name      = "CHUNK_END_SECONDS"
                      "Value.$" = "States.JsonToString($.chunk.end_seconds)"
                    },
                  ]
                }
              }
              Retry = [local.retry_policy]
              End   = true
            }
          }
        }
        ResultPath = "$.chunk_results"
        Next       = "MergeTranscripts"
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 5. MergeTranscripts
      #
      # Reassembles per-chunk transcripts into a single transcript.
      # The Lambda is responsible for deduplicating the overlapping
      # window; the spec is a 30s overlap with stitching at the
      # earliest matching token sequence, but the algorithm choice
      # lives entirely inside the Lambda.
      # -------------------------------------------------------------------
      MergeTranscripts = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.merge_transcripts
          Payload = {
            "ingestion_id.$"  = "$.chunking.ingestion_id"
            "user_id.$"       = "$.chunking.user_id"
            "chunk_results.$" = "$.chunk_results"
          }
        }
        ResultSelector = {
          "merged_transcript_s3_uri.$" = "$.Payload.merged_transcript_s3_uri"
          "ingestion_id.$"             = "$.Payload.ingestion_id"
          "user_id.$"                  = "$.Payload.user_id"
        }
        ResultPath = "$.merged"
        Next       = "WriteFinalTranscriptFromChunks"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 6. SubmitBatchSingle (short-audio path)
      #
      # Single Batch job for files under the threshold. Skips
      # chunking, merging, and the parallel Map. The Batch job's
      # output is the final transcript directly.
      # -------------------------------------------------------------------
      SubmitBatchSingle = {
        Type     = "Task"
        Resource = "arn:aws:states:::batch:submitJob.sync"
        Parameters = {
          "JobName.$"   = "States.Format('${local.batch_job_definition_name}-{}', $.ingestion_id)"
          JobQueue      = local.batch_job_queue_name
          JobDefinition = local.batch_job_definition_name
          ContainerOverrides = {
            Environment = [
              {
                Name      = "INGESTION_ID"
                "Value.$" = "$.ingestion_id"
              },
              {
                Name      = "AUDIO_S3_URI"
                "Value.$" = "$.audio_s3_uri"
              },
              {
                Name  = "MODE"
                Value = "single"
              },
            ]
          }
        }
        ResultPath = "$.batch_result"
        Next       = "WriteFinalTranscriptFromSingle"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 7a. WriteFinalTranscriptFromChunks (long-audio path tail)
      #
      # Writes the merged transcript to the transcripts bucket and
      # flips the ingestion record's status to `transcribed`.
      # -------------------------------------------------------------------
      WriteFinalTranscriptFromChunks = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.write_final_transcript
          Payload = {
            "ingestion_id.$"             = "$.merged.ingestion_id"
            "user_id.$"                  = "$.merged.user_id"
            "merged_transcript_s3_uri.$" = "$.merged.merged_transcript_s3_uri"
            mode                         = "merged"
          }
        }
        ResultSelector = {
          "final_transcript_s3_uri.$" = "$.Payload.final_transcript_s3_uri"
          "status.$"                  = "$.Payload.status"
          "ingestion_id.$"            = "$.Payload.ingestion_id"
        }
        ResultPath = "$.final"
        Next       = "Success"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # 7b. WriteFinalTranscriptFromSingle (short-audio path tail)
      #
      # Same Lambda, different payload shape. The Lambda accepts
      # either `merged_transcript_s3_uri` or `single_transcript_s3_uri`
      # and writes a canonical final transcript.
      # -------------------------------------------------------------------
      WriteFinalTranscriptFromSingle = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.write_final_transcript
          Payload = {
            "ingestion_id.$" = "$.ingestion_id"
            "user_id.$"      = "$.user_id"
            mode             = "single"
          }
        }
        ResultSelector = {
          "final_transcript_s3_uri.$" = "$.Payload.final_transcript_s3_uri"
          "status.$"                  = "$.Payload.status"
          "ingestion_id.$"            = "$.Payload.ingestion_id"
        }
        ResultPath = "$.final"
        Next       = "Success"
        Retry      = [local.retry_policy]
        Catch      = local.catch_to_failure
      }

      # -------------------------------------------------------------------
      # NotifyFailure (terminal failure handler)
      #
      # Reached only via the Catch handlers above. Updates the
      # ingestion record's status to `failed` and emits a
      # `panakoes.transcribe / TranscriptionFailed` EventBridge event.
      # No retries here: if NotifyFailure itself fails, the catch
      # handler chain has already done its job and we want a fast
      # terminal Fail state to surface in CloudWatch.
      # -------------------------------------------------------------------
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.lambda_arns.notify_failure
          Payload = {
            "ingestion_id.$"      = "$.ingestion_id"
            "user_id.$"           = "$.user_id"
            "error.$"             = "$.error"
            "execution_arn.$"     = "$$.Execution.Id"
            "state_machine_arn.$" = "$$.StateMachine.Id"
          }
        }
        ResultPath = "$.failure_notification"
        Next       = "FailTerminal"
      }

      FailTerminal = {
        Type  = "Fail"
        Error = "TranscriptionPipelineFailed"
        Cause = "See $.error in execution input for the underlying state failure. NotifyFailure has updated the ingestion record and emitted the EventBridge event."
      }

      Success = {
        Type = "Succeed"
      }
    }
  }
}

# ---------------------------------------------------------------------------
# State machine
#
# `definition = jsonencode(local.state_machine_definition)` produces a
# valid Amazon States Language document. Terraform's plan output
# renders the JSON with diff-able structure when only individual
# states change. The `dependencies` block forces the IAM role policy
# to apply before the state machine itself, so the role's permissions
# are in place before the service tries to create the log delivery on
# our behalf.
# ---------------------------------------------------------------------------

resource "aws_sfn_state_machine" "long_audio" {
  name     = local.state_machine_name
  type     = "STANDARD"
  role_arn = aws_iam_role.state_machine.arn

  definition = jsonencode(local.state_machine_definition)

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [
    aws_iam_role_policy.state_machine,
  ]

  tags = local.common_tags
}
