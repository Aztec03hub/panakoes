variable "aws_region" {
  description = "AWS region for the SES resources. SES is a regional service; us-east-1 matches the rest of the dev environment."
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

variable "sender_domain" {
  description = "Domain identity verified with SES. Any `*@<domain>` address can be used as the SES Source once DKIM tokens land in Cloudflare DNS."
  type        = string
  default     = "lafayettelabs.com"
}

variable "primary_sender_email" {
  description = "Email-identity verification for the primary `From:` address. While SES is in sandbox mode the To: address must also be a verified identity; this email doubles as the smoke-test recipient."
  type        = string
  default     = "phil@lafayettelabs.com"
}
