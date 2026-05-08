variable "aws_region" {
  description = "AWS region the bootstrap resources are created in."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}
