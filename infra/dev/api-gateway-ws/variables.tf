variable "aws_region" {
  description = "AWS region for the dev streaming WebSocket API and supporting resources."
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

variable "stage_name" {
  description = "API Gateway v2 WebSocket stage name. Becomes the URL path segment on `wss://<api-id>.execute-api.<region>.amazonaws.com/<stage_name>`."
  type        = string
  default     = "dev"
}

variable "access_log_retention_days" {
  description = "Retention for the WebSocket access log group. 30 days mirrors the project default and the sibling HTTP API module."
  type        = number
  default     = 30
}

variable "frame_queue_visibility_timeout_seconds" {
  description = "Visibility timeout for the streaming frame SQS queue. Set to 60 seconds so a downstream consumer outage gives operators a full minute to redrive before messages reappear; tighten when the real consumer (gpu-spawner or streaming-router Lambda) lands and its processing time is characterized."
  type        = number
  default     = 60
}

variable "frame_queue_message_retention_seconds" {
  description = "Retention for in-flight frame messages on the SQS frame queue. 4 hours covers the longest expected streaming session per ADR-011 while keeping replay risk bounded; production tuning happens once the consumer lands."
  type        = number
  default     = 14400
}

variable "execution_logging_level" {
  description = "API Gateway v2 WebSocket per-route execution logging level. INFO emits one log line per integration request with status + integration error; ERROR only logs 4xx/5xx; OFF disables execution logs entirely (access logs still ship). Default INFO for the smoke deploy so route + integration failures surface in the per-API execution log group at `API-Gateway-Execution-Logs_<api-id>/<stage>`."
  type        = string
  default     = "INFO"
  validation {
    condition     = contains(["OFF", "ERROR", "INFO"], var.execution_logging_level)
    error_message = "execution_logging_level must be one of OFF, ERROR, INFO."
  }
}

variable "execution_data_trace_enabled" {
  description = "Enable API Gateway data-trace logging (full integration request + response payloads, including any audio frame bytes). Default false so we never accidentally write audio payloads or JWT-bearing client frames to CloudWatch. Flip to true under operator supervision during a debug window only."
  type        = bool
  default     = false
}

variable "throttling_burst_limit" {
  description = "Default per-route burst limit applied to every WebSocket route via the stage default_route_settings. Streaming sessions are low-fanout (one client per session) so a generous default avoids accidental throttling during smoke tests."
  type        = number
  default     = 500
}

variable "throttling_rate_limit" {
  description = "Default per-route steady-state rate limit (requests per second) applied to every WebSocket route via the stage default_route_settings."
  type        = number
  default     = 200
}
