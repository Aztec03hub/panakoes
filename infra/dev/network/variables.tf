variable "aws_region" {
  description = "AWS region for the dev environment VPC and all networking resources."
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

variable "vpc_cidr" {
  description = "CIDR block for the dev VPC. The 10.10.0.0/16 prefix distinguishes dev from staging (10.20) and prod (10.30) for future inter-environment peering."
  type        = string
  default     = "10.10.0.0/16"
}
