output "api_id" {
  description = "ID of the panakoes-dev-public HTTP API. Used by downstream modules that need to attach authorizers, build custom-domain mappings, or reference the API in CloudWatch dashboards."
  value       = aws_apigatewayv2_api.main.id
}

output "api_arn" {
  description = "ARN of the panakoes-dev-public HTTP API. Useful for building cross-account resource policies once the gateway is reachable from outside the dev account."
  value       = aws_apigatewayv2_api.main.arn
}

output "api_endpoint" {
  description = "Default endpoint of the HTTP API (`https://<api-id>.execute-api.<region>.amazonaws.com`). Custom-domain consumers should use the custom domain instead once it is provisioned."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "stage_name" {
  description = "Name of the deployed stage (`dev`)."
  value       = aws_apigatewayv2_stage.main.name
}

output "stage_invoke_url" {
  description = "Full invoke URL for the deployed stage (`<api_endpoint>/<stage_name>`). This is the URL the SvelteKit frontend's API client should call until a custom domain is wired."
  value       = aws_apigatewayv2_stage.main.invoke_url
}

output "stage_arn" {
  description = "ARN of the deployed stage. Required when a downstream module needs to attach a WAF web ACL or grant CloudWatch metric-stream access scoped to this stage."
  value       = aws_apigatewayv2_stage.main.arn
}

output "vpc_link_id" {
  description = "ID of the shared VPC Link the integrations route through. Future per-service modules that add their own integrations should reference this value rather than provisioning duplicate VPC Links (AWS quota: 10 per region)."
  value       = aws_apigatewayv2_vpc_link.main.id
}

output "vpc_link_arn" {
  description = "ARN of the shared VPC Link."
  value       = aws_apigatewayv2_vpc_link.main.arn
}

output "vpc_link_security_group_id" {
  description = "ID of the security group attached to the VPC Link's elastic network interfaces. Upstream NLB security groups must allow ingress from this group on the listener port."
  value       = aws_security_group.vpc_link.id
}

output "access_log_group_name" {
  description = "Name of the CloudWatch log group receiving API Gateway access logs."
  value       = aws_cloudwatch_log_group.access.name
}

output "access_log_group_arn" {
  description = "ARN of the CloudWatch log group receiving API Gateway access logs. Used by downstream subscription filters that ship access logs to the log-archive S3 bucket."
  value       = aws_cloudwatch_log_group.access.arn
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the API Gateway access log group. Switches to the observability-module key once that module ships."
  value       = local.log_kms_key_arn
}
