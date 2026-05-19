variable "aws_region" {
  description = "AWS region for the API Gateway custom domain. Must match the region of the HTTP API in `infra/dev/api-gateway/` (HTTP API v2 custom domains require the ACM cert and the API in the same region)."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used for tagging and resource naming."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}

variable "custom_domain_name" {
  description = "Public-facing hostname that fronts the dev HTTP API. Subdomain of `panakoes.com`, which is registered at Cloudflare (2026-05-07). Dev environment uses `api.panakoes.com` directly; production is a separate AWS account so the same hostname can be reused under `infra/prod/` without collision."
  type        = string
  default     = "api.panakoes.com"
}

variable "enable_domain_mapping" {
  description = "When true, the `aws_apigatewayv2_domain_name` and `aws_apigatewayv2_api_mapping` resources are created. Default true: the cert is already issued (multi-SAN cert from infra/dev/frontend, see `external_cert_arn`), so the two-phase apply is no longer necessary."
  type        = bool
  default     = true
}

variable "external_cert_arn" {
  description = "Optional ARN of an externally-managed ACM certificate in us-east-1 that covers `custom_domain_name`. Default points at the multi-SAN cert issued 2026-05-19 for the admin SPA wiring (covers admin/api/www/apex panakoes.com); using one cert across the frontend + API GW avoids managing a second ACM issuance + renewal lifecycle. Set to empty string to fall back to the module-managed `aws_acm_certificate.api` resource."
  type        = string
  default     = "arn:aws:acm:us-east-1:659225405128:certificate/1357aee6-a1b7-4903-924c-c2a8b7052683"

  validation {
    condition     = var.external_cert_arn == "" || can(regex("^arn:aws:acm:us-east-1:[0-9]+:certificate/", var.external_cert_arn))
    error_message = "external_cert_arn must be empty or an ACM cert ARN in us-east-1."
  }
}
