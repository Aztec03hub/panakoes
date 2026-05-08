variable "aws_region" {
  description = "AWS region for the dev API Gateway and supporting CloudWatch resources."
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

variable "stage_name" {
  description = "API Gateway stage name. The stage's invoke URL becomes `https://<api-id>.execute-api.<region>.amazonaws.com/<stage_name>`."
  type        = string
  default     = "dev"
}

variable "cors_allow_origins" {
  description = "List of origins permitted by the HTTP API CORS configuration. Includes the production marketing domain, the LaFayette Labs site, and the Vite dev server. Add staging / preview origins here when those environments land."
  type        = list(string)
  default = [
    "https://panakoes.com",
    "https://lafayettelabs.com",
    "http://localhost:5173",
  ]
}

variable "cors_allow_methods" {
  description = "HTTP methods permitted by the CORS configuration. OPTIONS is required for preflight; the rest cover the route surface in `main.tf`."
  type        = list(string)
  default     = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
}

variable "cors_allow_headers" {
  description = "Request headers the CORS configuration permits. Authorization carries the JWT bearer token; X-Request-Id is the project's correlation header (set by middleware-lib)."
  type        = list(string)
  default     = ["Authorization", "Content-Type", "X-Request-Id"]
}

variable "throttling_burst_limit" {
  description = "Maximum concurrent burst requests the stage permits before the token-bucket starts shedding load. 5000 is the dev default; production should be tuned against observed peaks."
  type        = number
  default     = 5000
}

variable "throttling_rate_limit" {
  description = "Steady-state per-second request rate the stage permits across all routes. 10000 RPS is well above expected dev load; raise in production after capacity testing."
  type        = number
  default     = 10000
}

variable "access_log_retention_days" {
  description = "CloudWatch Logs retention for the API Gateway access log group. 30 days matches the project default; long-term archive is the job of a downstream subscription filter to the log-archive S3 bucket."
  type        = number
  default     = 30
}

variable "vpc_link_subnet_count" {
  description = "Number of private subnets the VPC Link spans. The dev VPC has three private subnets (one per AZ); the link uses all of them so the integration survives a single-AZ NLB failure."
  type        = number
  default     = 3
}

variable "alarm_period_seconds" {
  description = "CloudWatch alarm evaluation period. 60s is the smallest window the AWS/ApiGateway namespace publishes by default; evaluation_periods controls the rolling window."
  type        = number
  default     = 60
}

variable "alarm_4xx_threshold_percent" {
  description = "4xx error rate threshold (percent) for the client-error alarm. Sustained breaches usually indicate a misbehaving frontend or a deployed contract change; investigation, not paging."
  type        = number
  default     = 10
}

variable "alarm_5xx_threshold_percent" {
  description = "5xx error rate threshold (percent) for the server-error alarm. A breach here means the API itself is failing; treat as paging-worthy."
  type        = number
  default     = 1
}

variable "alarm_p99_latency_ms" {
  description = "Integration latency p99 threshold (milliseconds). 2000 ms is the user-perceived-slow boundary for synchronous APIs; alarm fires when the upstream service starts slowing."
  type        = number
  default     = 2000
}

# ---------------------------------------------------------------------------
# Custom domain placeholder
#
# `panakoes_api_domain_name` and `panakoes_acm_certificate_arn` are
# wired through here so that the day we register the panakoes.com
# certificate in ACM and decide on a hostname (`api.panakoes.com` is
# the planned default), enabling the custom domain becomes a Terraform
# variable change instead of a structural rewrite. Leaving them empty
# keeps the optional `aws_apigatewayv2_domain_name` block in `main.tf`
# in its commented-out skeleton form.
# ---------------------------------------------------------------------------

variable "custom_domain_name" {
  description = "Optional custom domain name for the HTTP API (for example `api.panakoes.com`). Empty string keeps the custom-domain skeleton in main.tf disabled."
  type        = string
  default     = ""
}

variable "custom_domain_certificate_arn" {
  description = "ACM certificate ARN matching `custom_domain_name`. Empty string keeps the custom-domain skeleton in main.tf disabled. The certificate must be in the same region as the API for HTTP API custom domains."
  type        = string
  default     = ""
}
