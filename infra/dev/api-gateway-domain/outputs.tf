output "certificate_arn" {
  description = "ARN of the ACM certificate for the custom domain. Referenced by `aws_apigatewayv2_domain_name` once the cert validates."
  value       = aws_acm_certificate.api.arn
}

output "certificate_status" {
  description = "Validation status of the ACM certificate (PENDING_VALIDATION or ISSUED). Use this to confirm Cloudflare records propagated before flipping `enable_domain_mapping` to true."
  value       = aws_acm_certificate.api.status
}

output "certificate_validation_records" {
  description = "DNS records Phil must add to Cloudflare to validate the certificate. Each entry has `name`, `type` (always `CNAME` for DNS-validated certs), and `value` (the `_yyy.acm-validations.aws` target). Cloudflare's UI accepts these directly; set Proxy status to DNS only (gray cloud) so ACM can resolve the record."
  value = [
    for opt in aws_acm_certificate.api.domain_validation_options : {
      name  = opt.resource_record_name
      type  = opt.resource_record_type
      value = opt.resource_record_value
    }
  ]
}

output "custom_domain_name" {
  description = "Public-facing hostname configured for the dev HTTP API."
  value       = var.custom_domain_name
}

output "regional_domain_name" {
  description = "AWS-managed target hostname (`d-xxx.execute-api.us-east-1.amazonaws.com`). Phil creates a CNAME in Cloudflare from `api.dev.panakoes.com` to this value. Empty until `enable_domain_mapping = true`."
  value       = try(aws_apigatewayv2_domain_name.api[0].domain_name_configuration[0].target_domain_name, null)
}

output "regional_zone_id" {
  description = "Route 53 hosted-zone ID of the regional API Gateway domain. Surfaced for future Route 53 migration; unused while DNS lives in Cloudflare. Empty until `enable_domain_mapping = true`."
  value       = try(aws_apigatewayv2_domain_name.api[0].domain_name_configuration[0].hosted_zone_id, null)
}

output "api_mapping_id" {
  description = "ID of the `aws_apigatewayv2_api_mapping` connecting the custom domain to the HTTP API stage. Empty until `enable_domain_mapping = true`."
  value       = try(aws_apigatewayv2_api_mapping.api[0].id, null)
}
