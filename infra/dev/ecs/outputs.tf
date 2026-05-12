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
# NLB listener ARN map (the API Gateway integration contract)
#
# This is THE contract the api-gateway module reads from
# `data.terraform_remote_state.ecs.outputs.nlb_listener_arns` when
# `var.discover_ecs_nlbs = true`. The map is keyed by service name;
# the api-gateway module's `local.routes` map references the same
# service-name keys, and `local.active_routes` filters routes to the
# discovered set.
#
# Adding a new service to this module:
#   1. Provision its NLB + target group + listener (mirror auth.tf).
#   2. Provision its task definition + ECS service.
#   3. Append its listener ARN to this map under the service name
#      api-gateway uses (auth, ingestion-api, summarization,
#      notification, query-api, session-manager, billing).
#   4. Apply this module, then re-apply api-gateway.
# ---------------------------------------------------------------------------
output "nlb_listener_arns" {
  description = "Map of service name to NLB listener ARN. Consumed by infra/dev/api-gateway via terraform_remote_state to build VPC Link integrations. Maps every backend service this module owns; new services append entries here."
  value = {
    auth                = aws_lb_listener.auth.arn
    "cost-api"          = aws_lb_listener.cost_api.arn
    "admin-api"         = aws_lb_listener.admin_api.arn
    "ingestion-api"     = aws_lb_listener.ingestion_api.arn
    "query-api"         = aws_lb_listener.query_api.arn
    "gpu-spawner"       = aws_lb_listener.gpu_spawner.arn
    "health-aggregator" = aws_lb_listener.health_aggregator.arn
    summarization       = aws_lb_listener.summarization.arn
    notification        = aws_lb_listener.notification.arn
    "session-manager"   = aws_lb_listener.session_manager.arn
    billing             = aws_lb_listener.billing.arn
  }
}

# ---------------------------------------------------------------------------
# Auth service surface
# ---------------------------------------------------------------------------

output "auth_nlb_arn" {
  description = "ARN of the internal NLB fronting the auth service. Useful for CloudWatch alarms and cross-module diagnostics."
  value       = aws_lb.auth.arn
}

output "auth_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the auth service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.auth.dns_name
}

output "auth_target_group_arn" {
  description = "ARN of the auth service NLB target group. Surfaced so downstream blue/green deployment tooling (CodeDeploy, etc.) can attach if introduced later."
  value       = aws_lb_target_group.auth.arn
}

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

output "cost_api_nlb_arn" {
  description = "ARN of the internal NLB fronting the cost-api service."
  value       = aws_lb.cost_api.arn
}

output "cost_api_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the cost-api service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.cost_api.dns_name
}

output "cost_api_target_group_arn" {
  description = "ARN of the cost-api NLB target group."
  value       = aws_lb_target_group.cost_api.arn
}

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

output "admin_api_nlb_arn" {
  description = "ARN of the internal NLB fronting the admin-api service."
  value       = aws_lb.admin_api.arn
}

output "admin_api_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the admin-api service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.admin_api.dns_name
}

output "admin_api_target_group_arn" {
  description = "ARN of the admin-api NLB target group."
  value       = aws_lb_target_group.admin_api.arn
}

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

output "ingestion_api_nlb_arn" {
  description = "ARN of the internal NLB fronting the ingestion-api service."
  value       = aws_lb.ingestion_api.arn
}

output "ingestion_api_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the ingestion-api service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.ingestion_api.dns_name
}

output "ingestion_api_target_group_arn" {
  description = "ARN of the ingestion-api NLB target group."
  value       = aws_lb_target_group.ingestion_api.arn
}

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

output "query_api_nlb_arn" {
  description = "ARN of the internal NLB fronting the query-api service."
  value       = aws_lb.query_api.arn
}

output "query_api_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the query-api service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.query_api.dns_name
}

output "query_api_target_group_arn" {
  description = "ARN of the query-api NLB target group."
  value       = aws_lb_target_group.query_api.arn
}

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

output "summarization_nlb_arn" {
  description = "ARN of the internal NLB fronting the summarization service."
  value       = aws_lb.summarization.arn
}

output "summarization_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the summarization service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.summarization.dns_name
}

output "summarization_target_group_arn" {
  description = "ARN of the summarization NLB target group."
  value       = aws_lb_target_group.summarization.arn
}

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

output "notification_nlb_arn" {
  description = "ARN of the internal NLB fronting the notification service."
  value       = aws_lb.notification.arn
}

output "notification_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the notification service."
  value       = aws_lb.notification.dns_name
}

output "notification_target_group_arn" {
  description = "ARN of the notification NLB target group."
  value       = aws_lb_target_group.notification.arn
}

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

output "session_manager_nlb_arn" {
  description = "ARN of the internal NLB fronting the session-manager service."
  value       = aws_lb.session_manager.arn
}

output "session_manager_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the session-manager service."
  value       = aws_lb.session_manager.dns_name
}

output "session_manager_target_group_arn" {
  description = "ARN of the session-manager NLB target group."
  value       = aws_lb_target_group.session_manager.arn
}

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

output "billing_nlb_arn" {
  description = "ARN of the internal NLB fronting the billing service."
  value       = aws_lb.billing.arn
}

output "billing_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the billing service."
  value       = aws_lb.billing.dns_name
}

output "billing_target_group_arn" {
  description = "ARN of the billing NLB target group."
  value       = aws_lb_target_group.billing.arn
}

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

output "gpu_spawner_nlb_arn" {
  description = "ARN of the internal NLB fronting the gpu-spawner service."
  value       = aws_lb.gpu_spawner.arn
}

output "gpu_spawner_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the gpu-spawner service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.gpu_spawner.dns_name
}

output "gpu_spawner_target_group_arn" {
  description = "ARN of the gpu-spawner NLB target group."
  value       = aws_lb_target_group.gpu_spawner.arn
}

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

output "health_aggregator_nlb_arn" {
  description = "ARN of the internal NLB fronting the health-aggregator service."
  value       = aws_lb.health_aggregator.arn
}

output "health_aggregator_nlb_dns_name" {
  description = "DNS name of the internal NLB fronting the health-aggregator service. Reachable only from inside the VPC; the API Gateway VPC Link routes here."
  value       = aws_lb.health_aggregator.dns_name
}

output "health_aggregator_target_group_arn" {
  description = "ARN of the health-aggregator NLB target group."
  value       = aws_lb_target_group.health_aggregator.arn
}

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
