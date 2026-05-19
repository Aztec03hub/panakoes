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

variable "admin_domain_aliases" {
  description = "List of fully-qualified domain names to attach as CloudFront Aliases (CNAMEs) on the admin distribution. Empty list keeps the distribution at its native *.cloudfront.net hostname only. Default `[\"admin.panakoes.com\"]` matches the dev environment; a matching ACM certificate in us-east-1 must be wired via `admin_acm_certificate_arn` and Cloudflare must have a CNAME `admin.panakoes.com -> <distribution-domain>.cloudfront.net` (DNS-only, proxy off). CloudFront REJECTS any aliases listed here that are not covered by the cert, so the two variables move together."
  type        = list(string)
  default     = ["admin.panakoes.com"]
}

variable "admin_acm_certificate_arn" {
  description = "ARN of the ACM certificate in us-east-1 that covers every name in `admin_domain_aliases`. Empty string keeps the CloudFront-default `*.cloudfront.net` cert in place. Default is the multi-SAN cert issued 2026-05-19 (admin + api + apex + www panakoes.com). The cert MUST be in us-east-1: CloudFront is a global service but reads viewer certificates exclusively from us-east-1 (this is an AWS limitation, not a Panakoes choice). Cert is pending DNS validation at first apply; CloudFront alias attach fails until the cert is ISSUED, so wait for validation before running apply."
  type        = string
  default     = "arn:aws:acm:us-east-1:659225405128:certificate/1357aee6-a1b7-4903-924c-c2a8b7052683"

  validation {
    condition     = var.admin_acm_certificate_arn == "" || can(regex("^arn:aws:acm:us-east-1:[0-9]+:certificate/", var.admin_acm_certificate_arn))
    error_message = "admin_acm_certificate_arn must be empty or an ACM cert ARN in us-east-1."
  }
}
