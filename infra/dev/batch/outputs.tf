output "compute_env_arn" {
  description = "ARN of the dev transcription Batch compute environment. Downstream Step Functions / Lambdas wire this in when submitting jobs that need to be queue-routed by ARN."
  value       = aws_batch_compute_environment.transcribe.arn
}

output "compute_env_name" {
  description = "Name of the dev transcription Batch compute environment."
  value       = aws_batch_compute_environment.transcribe.name
}

output "job_queue_arn" {
  description = "ARN of the transcription job queue. The async transcription dispatcher (Step Functions or the event-router Lambda) submits jobs to this queue."
  value       = aws_batch_job_queue.transcribe.arn
}

output "job_queue_name" {
  description = "Name of the transcription job queue."
  value       = aws_batch_job_queue.transcribe.name
}

output "job_def_arn" {
  description = "ARN of the transcription job definition. Job submitters reference this (latest revision) when calling SubmitJob."
  value       = aws_batch_job_definition.transcribe.arn
}

output "job_def_name" {
  description = "Name of the transcription job definition."
  value       = aws_batch_job_definition.transcribe.name
}

output "system_alerts_topic_arn" {
  description = "ARN of the project-wide system-alerts SNS topic, consumed from `infra/dev/events/` via terraform_remote_state and not owned by this module."
  value       = local.system_alerts_topic_arn
}

output "batch_log_group_name" {
  description = "Name of the CloudWatch log group the Batch job containers write to via the awslogs driver."
  value       = aws_cloudwatch_log_group.batch.name
}
