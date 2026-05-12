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
  description = "Public-facing hostname that fronts the dev HTTP API. Subdomain of `panakoes.com`, which is registered at Cloudflare (2026-05-07). The dev environment uses `api.dev.panakoes.com` to keep environment separation explicit; production will use `api.panakoes.com` in a parallel module under `infra/prod/`."
  type        = string
  default     = "api.dev.panakoes.com"
}

variable "enable_domain_mapping" {
  description = "When true, the `aws_apigatewayv2_domain_name` and `aws_apigatewayv2_api_mapping` resources are created. Default false: the first apply provisions ONLY the ACM certificate so Phil can add the DNS-validation CNAME to Cloudflare; once the cert flips to ISSUED (visible via `aws acm describe-certificate`), flip this to true and apply again to wire the domain + mapping. This two-phase apply keeps the second resource from blocking the apply on a cert that has not yet validated."
  type        = bool
  default     = false
}
