output "task_role_arns" {
  description = "Map of service name to the ARN of its runtime IAM (task) role. Downstream Terraform configs (ECS task definitions, Lambda function definitions) wire this in as `task_role_arn` / `role` so the application code runs with the least-privilege identity defined in this module."
  value = {
    ingestion-api      = aws_iam_role.ingestion_api.arn
    summarization      = aws_iam_role.summarization.arn
    notification       = aws_iam_role.notification.arn
    query-api          = aws_iam_role.query_api.arn
    auth               = aws_iam_role.auth.arn
    session-manager    = aws_iam_role.session_manager.arn
    gpu-spawner        = aws_iam_role.gpu_spawner.arn
    transcriber-batch  = aws_iam_role.transcriber_batch.arn
    transcriber-stream = aws_iam_role.transcriber_stream.arn
    event-router       = aws_iam_role.event_router.arn
    transcribe-worker  = aws_iam_role.transcribe_worker.arn
    billing            = aws_iam_role.billing.arn
    cost-api           = aws_iam_role.cost_api.arn
    admin-api          = aws_iam_role.admin_api.arn
  }
}

output "task_role_names" {
  description = "Map of service name to the name of its runtime IAM role. Useful for IAM-policy attachments downstream that need the role name rather than the ARN."
  value = {
    ingestion-api      = aws_iam_role.ingestion_api.name
    summarization      = aws_iam_role.summarization.name
    notification       = aws_iam_role.notification.name
    query-api          = aws_iam_role.query_api.name
    auth               = aws_iam_role.auth.name
    session-manager    = aws_iam_role.session_manager.name
    gpu-spawner        = aws_iam_role.gpu_spawner.name
    transcriber-batch  = aws_iam_role.transcriber_batch.name
    transcriber-stream = aws_iam_role.transcriber_stream.name
    event-router       = aws_iam_role.event_router.name
    transcribe-worker  = aws_iam_role.transcribe_worker.name
    billing            = aws_iam_role.billing.name
    cost-api           = aws_iam_role.cost_api.name
    admin-api          = aws_iam_role.admin_api.name
  }
}

output "execution_role_arns" {
  description = "Map of ECS service name to the ARN of its task execution role. Downstream ECS task definitions wire this in as `execution_role_arn`. Only ECS services have an execution role; Lambda services use the task role as the execution identity."
  value       = { for k, v in aws_iam_role.execution : k => v.arn }
}

output "execution_role_names" {
  description = "Map of ECS service name to its execution role name."
  value       = { for k, v in aws_iam_role.execution : k => v.name }
}

output "gpu_instance_role_arn" {
  description = "ARN of the IAM role attached to GPU EC2 instances spawned by gpu-spawner. Surfaced separately so the streaming AMI bring-up Terraform can attach additional permissions to this role without re-deriving the ARN."
  value       = aws_iam_role.gpu_instance.arn
}

output "gpu_instance_profile_name" {
  description = "Name of the IAM instance profile gpu-spawner attaches when calling ec2:RunInstances. Surfaced for the Lambda's environment variable wiring."
  value       = aws_iam_instance_profile.gpu_instance.name
}

output "gpu_instance_profile_arn" {
  description = "ARN of the IAM instance profile attached to GPU EC2 instances."
  value       = aws_iam_instance_profile.gpu_instance.arn
}

output "assume_role_policies_summary" {
  description = "Human-readable summary of which trust principal each service role uses. Useful for auditability and debugging."
  value = {
    ingestion-api      = "ecs-tasks.amazonaws.com"
    summarization      = "ecs-tasks.amazonaws.com"
    notification       = "ecs-tasks.amazonaws.com"
    query-api          = "ecs-tasks.amazonaws.com"
    auth               = "ecs-tasks.amazonaws.com"
    session-manager    = "ecs-tasks.amazonaws.com"
    billing            = "ecs-tasks.amazonaws.com"
    cost-api           = "ecs-tasks.amazonaws.com"
    admin-api          = "ecs-tasks.amazonaws.com"
    gpu-spawner        = "lambda.amazonaws.com"
    transcriber-batch  = "lambda.amazonaws.com"
    transcriber-stream = "ec2.amazonaws.com"
    event-router       = "lambda.amazonaws.com"
    transcribe-worker  = "lambda.amazonaws.com"
    gpu-instance       = "ec2.amazonaws.com"
  }
}
