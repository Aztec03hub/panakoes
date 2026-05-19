output "certificate_arn" {
  description = "ARN of the ACM certificate the custom domain uses. Either the external cert provided via `var.external_cert_arn`, or the module-managed cert when no external one is set."
  value       = local.cert_arn
}

output "certificate_status" {
  description = "Validation status of the module-managed cert (PENDING_VALIDATION or ISSUED). Null when an external cert is in use (its status is managed outside this module)."
  value       = try(aws_acm_certificate.api[0].status, null)
}

output "certificate_validation_records" {
  description = "DNS records to add to Cloudflare to validate the module-managed cert. Null when an external cert is in use (its validation is managed outside this module)."
  value = try([
    for opt in aws_acm_certificate.api[0].domain_validation_options : {
      name  = opt.resource_record_name
      type  = opt.resource_record_type
      value = opt.resource_record_value
    }
  ], null)
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
