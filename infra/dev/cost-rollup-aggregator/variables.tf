variable "aws_region" {
  description = "AWS region for the cost-rollup-aggregator resources. Cost Explorer is global but the Lambda + Scheduler + log-group provider needs a region."
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

variable "schedule_expression" {
  description = "EventBridge Scheduler cron expression. Default fires daily at 02:00 UTC, the lowest-traffic window for the AWS billing API and 2 hours past the UTC-day boundary so CE has settled refreshed numbers for yesterday."
  type        = string
  default     = "cron(0 2 * * ? *)"
}

variable "lambda_memory_mb" {
  description = "Lambda memory size in MB. The aggregator's working set is small (one CE response + a handful of DDB writes); 256 MB is plenty and the lower memory saves invocation cost."
  type        = number
  default     = 256
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout in seconds. CE responses can take 1-3 seconds and we follow pagination, so 5 minutes is a generous ceiling that keeps a stuck aggregator from billing unbounded duration without artificially capping a slow CE day."
  type        = number
  default     = 300
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the function's log group. Matches the 30-day floor used by `infra/dev/observability/` per the locked decision in CLAUDE.md."
  type        = number
  default     = 30
}
