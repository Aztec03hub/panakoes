variable "aws_region" {
  description = "AWS region used by the AWS provider. The resources in this module are global (IAM), but the provider still requires a region."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose Actions workflows are allowed to assume the IAM role via OIDC."
  type        = string
  default     = "Aztec03hub/panakoes"
}
