variable "aws_region" {
  description = "AWS region for the dev transcribe-worker resources."
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

variable "lambda_image_tag" {
  description = "Container image tag (in the panakoes-dev-transcribe-worker ECR repo) the Lambda runs. Operator updates this on each deploy. Default `batch-dispatch-20260520-015948` is the build that introduced the AWS Batch dispatch path (`TRANSCRIBER_BACKEND=batch`); the prior `fix-manifest-1` build only knows the synchronous Groq/OpenAI paths."
  type        = string
  default     = "batch-dispatch-20260520-015948"
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrency for the worker Lambda. Bounds the parallel hammer against Groq's free-tier rate limits. 5 is a safe default for dev; raise as paid-tier capacity grows."
  type        = number
  default     = 5
}
