variable "aws_region" {
  description = "AWS region for the budgets module. AWS Budgets is a global service but the provider still requires a region for API calls."
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

variable "alert_email" {
  description = "Email address that receives AWS Budgets notifications. On first apply AWS sends an SNS-style subscription confirmation email; the operator MUST click the confirmation link before alerts deliver. Override in prod to a shared distribution list."
  type        = string
  default     = "phil@lafayettelabs.com"
}

variable "account_budget_limit_usd" {
  description = "Monthly cap for the account-wide budget, in USD. The account currently runs ~$5/month so $100 provides a 20x cushion as services come online."
  type        = number
  default     = 100
}

variable "ec2_budget_limit_usd" {
  description = "Monthly cap for the EC2 service-specific budget. Covers GPU spot capacity (transcribe-worker fan-out), NAT Gateway hours (~$33/mo at us-east-1), and any t3 probe instances."
  type        = number
  default     = 35
}

variable "aurora_budget_limit_usd" {
  description = "Monthly cap for the Aurora Serverless v2 budget. auth-db scales 0.5-4 ACUs; at minimum-warm baseline runs ~$0.06/hr = ~$43/mo, but actual dev usage idles much lower. $15 catches sustained warm-state regressions."
  type        = number
  default     = 15
}

variable "bedrock_budget_limit_usd" {
  description = "Monthly cap for the Bedrock budget. Covers Claude Haiku 4.5 (default summarization) and Claude Sonnet 4.6 (paid-tier deep summaries) call passthrough. Provisioned ahead of Bedrock spend appearing in Cost Explorer so first-month overruns trip an alert immediately."
  type        = number
  default     = 25
}

variable "cloudfront_s3_budget_limit_usd" {
  description = "Monthly cap for the combined CloudFront + S3 budget (static SPA hosting + asset buckets). Both services are AWS Free Tier-heavy at dev scale; $5 traps a meaningful overrun (1000+ GB egress or comparable)."
  type        = number
  default     = 5
}

variable "project_tag_budget_limit_usd" {
  description = "Monthly cap for the per-environment tag-scoped budget filtered to resources tagged `Project=panakoes`. Mirrors the account-wide cap; lets future staging / prod environments roll into the same account and still track separately by tag."
  type        = number
  default     = 100
}
