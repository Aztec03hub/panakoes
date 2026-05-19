output "state_machine_arn" {
  description = "ARN of the long-audio Step Functions state machine. Downstream services (the event-router Lambda fanning out the AudioUploaded event) call StartExecution against this ARN to begin a transcription run."
  value       = aws_sfn_state_machine.long_audio.arn
}

output "state_machine_name" {
  description = "Name of the long-audio Step Functions state machine. Useful for AWS CLI execution lookups (`aws stepfunctions list-executions --state-machine-arn ...`)."
  value       = aws_sfn_state_machine.long_audio.name
}

output "state_machine_role_arn" {
  description = "ARN of the IAM role the state machine assumes at runtime. Surfaced so the IAM module can grant downstream services iam:PassRole on this exact ARN if a different orchestrator needs to instantiate or update the state machine."
  value       = aws_iam_role.state_machine.arn
}

output "log_group_name" {
  description = "Name of the CloudWatch log group receiving state-machine execution events."
  value       = aws_cloudwatch_log_group.state_machine.name
}

output "log_group_arn" {
  description = "ARN of the CloudWatch log group receiving state-machine execution events. Used by downstream subscription filters that ship execution events to the log-archive S3 bucket."
  value       = aws_cloudwatch_log_group.state_machine.arn
}

output "log_group_kms_key_arn" {
  description = "ARN of the KMS key encrypting the state-machine log group. W2-T4 extension: now returns the consolidated panakoes/logs CMK ARN provisioned by `infra/dev/kms/` (PR #365); the module-local aws_kms_key.fallback_log resource is retained for W2-T7 retirement but no longer encrypts the log group."
  value       = local.log_group_kms_arn
}

output "lambda_function_names" {
  description = "Map of state-machine logical role to the Lambda function name the role expects to find. None of these Lambdas exist yet; the state machine resolves them by name when an execution reaches that state. Documented here so downstream Lambda Terraform configs can pin to the same names."
  value = {
    detect_duration        = "${var.project_name}-${var.environment}-detect-duration"
    chunk_audio            = "${var.project_name}-${var.environment}-chunk-audio"
    merge_transcripts      = "${var.project_name}-${var.environment}-merge-transcripts"
    write_final_transcript = "${var.project_name}-${var.environment}-write-final-transcript"
    notify_failure         = "${var.project_name}-${var.environment}-notify-failure"
  }
}
