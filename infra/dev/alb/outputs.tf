output "alb_arn" {
  description = "ARN of the shared internal ALB. Consumed by the api-gateway module (W1-T3) to update the VPC Link integration."
  value       = aws_lb.this.arn
}

output "alb_dns_name" {
  description = "DNS name of the shared internal ALB."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Canonical hosted zone ID of the ALB (for Route 53 alias records if needed)."
  value       = aws_lb.this.zone_id
}

output "alb_sg_id" {
  description = "Security group ID of the ALB. ECS task security groups (W1-T4) must allow inbound from this SG on the service's container port."
  value       = aws_security_group.alb.id
}

output "listener_arn" {
  description = "ARN of the HTTP listener. Used by W1-T3 to reference the listener when wiring API GW."
  value       = aws_lb_listener.http.arn
}

output "target_group_arns" {
  description = "Map from service name (e.g. 'auth', 'admin-api') to target group ARN. Consumed by W1-T4 (ECS service load_balancers blocks) and W1-T3 (API GW integrations)."
  value       = { for k, tg in aws_lb_target_group.services : k => tg.arn }
}
