variable "aws_region" {
  description = "AWS region for the cost-anomaly-monitor resources. Cost Anomaly Detection is a global service but the provider needs a region for its API calls."
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
  description = "Email address that receives Cost Anomaly Detection alerts. AWS sends an SNS-style confirmation email to this address on first apply; the operator MUST click the confirmation link before alerts will deliver."
  type        = string
  default     = "phil@lafayettelabs.com"
}
