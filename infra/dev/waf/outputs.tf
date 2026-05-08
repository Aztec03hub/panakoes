output "web_acl_arn" {
  description = "ARN of the dev public-facing WAFv2 web ACL. Pass this to `aws_wafv2_web_acl_association` (ALB / API Gateway) or to a CloudFront distribution's `web_acl_id` field."
  value       = aws_wafv2_web_acl.public.arn
}

output "web_acl_id" {
  description = "ID of the dev public-facing WAFv2 web ACL. Useful for CLI lookups (`aws wafv2 get-web-acl --scope REGIONAL --id <id> --name <name>`)."
  value       = aws_wafv2_web_acl.public.id
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the WAF CloudWatch log group. Required in IAM policies that grant kms:Decrypt to log readers."
  value       = aws_kms_key.waf.arn
}

output "log_group_name" {
  description = "Name of the WAF CloudWatch log group. Note the `aws-waf-logs-` prefix is required by the WAF service."
  value       = aws_cloudwatch_log_group.waf.name
}

output "log_group_arn" {
  description = "ARN of the WAF CloudWatch log group. Used by downstream subscription filters that ship WAF logs to the log-archive S3 bucket."
  value       = aws_cloudwatch_log_group.waf.arn
}
