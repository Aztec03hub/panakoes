output "namespace_id" {
  description = "Cloud Map namespace ID consumed by ECS Service Connect configuration (W1-T4)."
  value       = aws_service_discovery_private_dns_namespace.this.id
}

output "namespace_arn" {
  description = "Cloud Map namespace ARN."
  value       = aws_service_discovery_private_dns_namespace.this.arn
}
