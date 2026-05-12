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

variable "rate_limit_per_5min" {
  description = "Per-IP request budget over a rolling 5-minute window. The WAF blocks any source IP that exceeds this count. 2000 is a deliberate dashboard default: the admin SPA loads its full bundle (HTML, JS chunks, CSS, fonts, sourcemaps if enabled) in well under 100 requests on first load, and a refresh-heavy operator hitting every tab still tops out around a few hundred per 5 minutes. 2000 leaves an order of magnitude of headroom for legitimate traffic while still tripping on credential-stuffing, scraping, and naive denial-of-service."
  type        = number
  default     = 2000
}

variable "log_retention_days" {
  description = "Retention window for the WAF CloudWatch log group. 30 days mirrors the rest of the dev observability stack; long-term archive lives in S3 via the future log-export subscription filters."
  type        = number
  default     = 30
}
