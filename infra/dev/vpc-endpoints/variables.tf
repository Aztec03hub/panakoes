variable "aws_region" {
  description = "AWS region for the dev environment VPC endpoints. Endpoint service names are region-scoped (`com.amazonaws.<region>.<service>`)."
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
