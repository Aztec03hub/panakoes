output "web_acl_arn" {
  description = "ARN of the dev CloudFront-scoped WAFv2 web ACL. Consumed by `infra/dev/frontend/` via terraform_remote_state and set on the CloudFront distribution's `web_acl_id` attribute. CloudFront distributions use ARNs for this attribute despite the field name."
  value       = aws_wafv2_web_acl.cloudfront.arn
}

output "web_acl_id" {
  description = "ID of the dev CloudFront-scoped WAFv2 web ACL. Useful for CLI lookups (`aws wafv2 get-web-acl --scope CLOUDFRONT --region us-east-1 --id <id> --name <name>`)."
  value       = aws_wafv2_web_acl.cloudfront.id
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the CloudFront WAF CloudWatch log group. Required in IAM policies that grant kms:Decrypt to log readers."
  value       = aws_kms_key.waf.arn
}

output "log_group_name" {
  description = "Name of the CloudFront WAF CloudWatch log group. Note the `aws-waf-logs-` prefix is required by the WAF service."
  value       = aws_cloudwatch_log_group.waf.name
}

output "log_group_arn" {
  description = "ARN of the CloudFront WAF CloudWatch log group. Used by downstream subscription filters that ship WAF logs to the log-archive S3 bucket."
  value       = aws_cloudwatch_log_group.waf.arn
}
