variable "aws_region" {
  description = "AWS region for the dev streaming frame-pool resources."
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

variable "pool_size" {
  description = "Number of SQS queues in the pre-allocated streaming-frame pool. Default 32 per the design doc's peak-concurrency target (10 sessions) with 3x headroom for surge. Tunable upward; raising the cap requires a terraform-apply and proportional bump in the gpu-spawner's idle-reaper batch size."
  type        = number
  default     = 32
}
