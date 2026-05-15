output "cluster_arn" {
  description = "ARN of the panakoes-dev ECS cluster. Required by downstream modules that attach scheduled tasks, capacity-provider strategies, or CloudWatch alarms scoped to the cluster."
  value       = aws_ecs_cluster.main.arn
}

output "cluster_name" {
  description = "Name of the panakoes-dev ECS cluster. Convenience companion to cluster_arn for AWS APIs that take the bare name."
  value       = aws_ecs_cluster.main.name
}

output "cluster_id" {
  description = "ID of the panakoes-dev ECS cluster (same as the ARN; surfaced separately for compatibility with `aws_ecs_service.cluster`)."
  value       = aws_ecs_cluster.main.id
}

# ---------------------------------------------------------------------------
# Auth service surface
# ---------------------------------------------------------------------------

output "auth_task_definition_arn" {
  description = "ARN of the auth task definition (latest Terraform-managed revision). Out-of-band deploys via `aws ecs update-service --force-new-deployment` may produce newer revisions; this output reflects what Terraform last applied."
  value       = aws_ecs_task_definition.auth.arn
}

output "auth_task_definition_family" {
  description = "Family name of the auth task definition (`panakoes-dev-auth`)."
  value       = aws_ecs_task_definition.auth.family
}

output "auth_service_name" {
  description = "Name of the auth ECS service (`panakoes-dev-auth`)."
  value       = aws_ecs_service.auth.name
}

output "auth_service_arn" {
  description = "ARN of the auth ECS service."
  value       = aws_ecs_service.auth.id
}

output "auth_task_security_group_id" {
  description = "Security group ID attached to auth Fargate tasks. Reference this from the auth-db module's Aurora SG to replace the VPC-CIDR ingress rule with a tight SG-to-SG rule (planned tightening pass)."
  value       = aws_security_group.auth_task.id
}

# ---------------------------------------------------------------------------
# cost-api service surface
# ---------------------------------------------------------------------------

output "cost_api_task_definition_arn" {
  description = "ARN of the cost-api task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.cost_api.arn
}

output "cost_api_task_definition_family" {
  description = "Family name of the cost-api task definition (`panakoes-dev-cost-api`)."
  value       = aws_ecs_task_definition.cost_api.family
}

output "cost_api_service_name" {
  description = "Name of the cost-api ECS service (`panakoes-dev-cost-api`)."
  value       = aws_ecs_service.cost_api.name
}

output "cost_api_service_arn" {
  description = "ARN of the cost-api ECS service."
  value       = aws_ecs_service.cost_api.id
}

output "cost_api_task_security_group_id" {
  description = "Security group ID attached to cost-api Fargate tasks."
  value       = aws_security_group.cost_api_task.id
}

# ---------------------------------------------------------------------------
# admin-api service surface
# ---------------------------------------------------------------------------

output "admin_api_task_definition_arn" {
  description = "ARN of the admin-api task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.admin_api.arn
}

output "admin_api_task_definition_family" {
  description = "Family name of the admin-api task definition (`panakoes-dev-admin-api`)."
  value       = aws_ecs_task_definition.admin_api.family
}

output "admin_api_service_name" {
  description = "Name of the admin-api ECS service (`panakoes-dev-admin-api`)."
  value       = aws_ecs_service.admin_api.name
}

output "admin_api_service_arn" {
  description = "ARN of the admin-api ECS service."
  value       = aws_ecs_service.admin_api.id
}

output "admin_api_task_security_group_id" {
  description = "Security group ID attached to admin-api Fargate tasks."
  value       = aws_security_group.admin_api_task.id
}

# ---------------------------------------------------------------------------
# ingestion-api service surface
# ---------------------------------------------------------------------------

output "ingestion_api_task_definition_arn" {
  description = "ARN of the ingestion-api task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.ingestion_api.arn
}

output "ingestion_api_task_definition_family" {
  description = "Family name of the ingestion-api task definition (`panakoes-dev-ingestion-api`)."
  value       = aws_ecs_task_definition.ingestion_api.family
}

output "ingestion_api_service_name" {
  description = "Name of the ingestion-api ECS service (`panakoes-dev-ingestion-api`)."
  value       = aws_ecs_service.ingestion_api.name
}

output "ingestion_api_service_arn" {
  description = "ARN of the ingestion-api ECS service."
  value       = aws_ecs_service.ingestion_api.id
}

output "ingestion_api_task_security_group_id" {
  description = "Security group ID attached to ingestion-api Fargate tasks."
  value       = aws_security_group.ingestion_api_task.id
}

# ---------------------------------------------------------------------------
# query-api service surface
# ---------------------------------------------------------------------------

output "query_api_task_definition_arn" {
  description = "ARN of the query-api task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.query_api.arn
}

output "query_api_task_definition_family" {
  description = "Family name of the query-api task definition (`panakoes-dev-query-api`)."
  value       = aws_ecs_task_definition.query_api.family
}

output "query_api_service_name" {
  description = "Name of the query-api ECS service (`panakoes-dev-query-api`)."
  value       = aws_ecs_service.query_api.name
}

output "query_api_service_arn" {
  description = "ARN of the query-api ECS service."
  value       = aws_ecs_service.query_api.id
}

output "query_api_task_security_group_id" {
  description = "Security group ID attached to query-api Fargate tasks."
  value       = aws_security_group.query_api_task.id
}

# ---------------------------------------------------------------------------
# summarization service surface
# ---------------------------------------------------------------------------

output "summarization_task_definition_arn" {
  description = "ARN of the summarization task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.summarization.arn
}

output "summarization_task_definition_family" {
  description = "Family name of the summarization task definition (`panakoes-dev-summarization`)."
  value       = aws_ecs_task_definition.summarization.family
}

output "summarization_service_name" {
  description = "Name of the summarization ECS service (`panakoes-dev-summarization`)."
  value       = aws_ecs_service.summarization.name
}

output "summarization_service_arn" {
  description = "ARN of the summarization ECS service."
  value       = aws_ecs_service.summarization.id
}

output "summarization_task_security_group_id" {
  description = "Security group ID attached to summarization Fargate tasks."
  value       = aws_security_group.summarization_task.id
}

# ---------------------------------------------------------------------------
# notification service surface
# ---------------------------------------------------------------------------

output "notification_task_definition_arn" {
  description = "ARN of the notification task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.notification.arn
}

output "notification_task_definition_family" {
  description = "Family name of the notification task definition (`panakoes-dev-notification`)."
  value       = aws_ecs_task_definition.notification.family
}

output "notification_service_name" {
  description = "Name of the notification ECS service (`panakoes-dev-notification`)."
  value       = aws_ecs_service.notification.name
}

output "notification_service_arn" {
  description = "ARN of the notification ECS service."
  value       = aws_ecs_service.notification.id
}

output "notification_task_security_group_id" {
  description = "Security group ID attached to notification Fargate tasks."
  value       = aws_security_group.notification_task.id
}

# ---------------------------------------------------------------------------
# session-manager service surface
# ---------------------------------------------------------------------------

output "session_manager_task_definition_arn" {
  description = "ARN of the session-manager task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.session_manager.arn
}

output "session_manager_task_definition_family" {
  description = "Family name of the session-manager task definition (`panakoes-dev-session-manager`)."
  value       = aws_ecs_task_definition.session_manager.family
}

output "session_manager_service_name" {
  description = "Name of the session-manager ECS service (`panakoes-dev-session-manager`)."
  value       = aws_ecs_service.session_manager.name
}

output "session_manager_service_arn" {
  description = "ARN of the session-manager ECS service."
  value       = aws_ecs_service.session_manager.id
}

output "session_manager_task_security_group_id" {
  description = "Security group ID attached to session-manager Fargate tasks."
  value       = aws_security_group.session_manager_task.id
}

# ---------------------------------------------------------------------------
# billing service surface
# ---------------------------------------------------------------------------

output "billing_task_definition_arn" {
  description = "ARN of the billing task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.billing.arn
}

output "billing_task_definition_family" {
  description = "Family name of the billing task definition (`panakoes-dev-billing`)."
  value       = aws_ecs_task_definition.billing.family
}

output "billing_service_name" {
  description = "Name of the billing ECS service (`panakoes-dev-billing`)."
  value       = aws_ecs_service.billing.name
}

output "billing_service_arn" {
  description = "ARN of the billing ECS service."
  value       = aws_ecs_service.billing.id
}

output "billing_task_security_group_id" {
  description = "Security group ID attached to billing Fargate tasks."
  value       = aws_security_group.billing_task.id
}

# ---------------------------------------------------------------------------
# gpu-spawner service surface
# ---------------------------------------------------------------------------

output "gpu_spawner_task_definition_arn" {
  description = "ARN of the gpu-spawner task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.gpu_spawner.arn
}

output "gpu_spawner_task_definition_family" {
  description = "Family name of the gpu-spawner task definition (`panakoes-dev-gpu-spawner`)."
  value       = aws_ecs_task_definition.gpu_spawner.family
}

output "gpu_spawner_service_name" {
  description = "Name of the gpu-spawner ECS service (`panakoes-dev-gpu-spawner`)."
  value       = aws_ecs_service.gpu_spawner.name
}

output "gpu_spawner_service_arn" {
  description = "ARN of the gpu-spawner ECS service."
  value       = aws_ecs_service.gpu_spawner.id
}

output "gpu_spawner_task_security_group_id" {
  description = "Security group ID attached to gpu-spawner Fargate tasks."
  value       = aws_security_group.gpu_spawner_task.id
}

# ---------------------------------------------------------------------------
# health-aggregator service surface
# ---------------------------------------------------------------------------

output "health_aggregator_task_definition_arn" {
  description = "ARN of the health-aggregator task definition (latest Terraform-managed revision)."
  value       = aws_ecs_task_definition.health_aggregator.arn
}

output "health_aggregator_task_definition_family" {
  description = "Family name of the health-aggregator task definition (`panakoes-dev-health-aggregator`)."
  value       = aws_ecs_task_definition.health_aggregator.family
}

output "health_aggregator_service_name" {
  description = "Name of the health-aggregator ECS service (`panakoes-dev-health-aggregator`)."
  value       = aws_ecs_service.health_aggregator.name
}

output "health_aggregator_service_arn" {
  description = "ARN of the health-aggregator ECS service."
  value       = aws_ecs_service.health_aggregator.id
}

output "health_aggregator_task_security_group_id" {
  description = "Security group ID attached to health-aggregator Fargate tasks."
  value       = aws_security_group.health_aggregator_task.id
}
