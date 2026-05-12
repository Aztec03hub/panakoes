variable "aws_region" {
  description = "AWS region for the dev environment frontend resources. CloudFront is a global service but the S3 origin bucket and the access-log bucket live in this region."
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

variable "associate_waf" {
  description = "Whether to associate the dev cloudfront-waf web ACL with the CloudFront distribution. Defaults to true now that infra/dev/cloudfront-waf/ provisions a CloudFront-scoped ACL in us-east-1. Flip to false to detach the ACL (e.g. emergency mitigation of a false-positive that is blocking real users)."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Number of days CloudFront access logs are retained in the dedicated S3 log bucket before lifecycle expiry. Default 90 days matches the brief."
  type        = number
  default     = 90
}
