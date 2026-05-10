variable "aws_region" {
  description = "AWS region for the dev environment auth-db cluster."
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

variable "engine_version" {
  description = "Aurora PostgreSQL engine version. Pinned to 16.4 (latest LTS minor on the 16 line at module-authoring time). Bump deliberately; minor-version bumps are an in-place upgrade path."
  type        = string
  default     = "16.4"
}

variable "min_capacity_acu" {
  description = "Aurora Serverless v2 minimum capacity in Aurora Capacity Units. `0` enables true scale-to-zero auto-pause (announced by AWS Nov 2024; supported on Aurora PostgreSQL 13.15+, 14.12+, 15.7+, 16.3+, and Aurora MySQL 3.08+). The cluster idles at 0 ACU = $0/hr after `seconds_until_auto_pause` seconds of no connections, then cold-starts on the next connection (typical resume ~15 sec per AWS). Dev default is `0` for the cost story; production with sustained load should override to a non-zero floor (typically 0.5) to avoid cold-start latency."
  type        = number
  default     = 0
}

variable "max_capacity_acu" {
  description = "Aurora Serverless v2 maximum capacity in Aurora Capacity Units. 4 ACU is enough headroom for any dev integration test sweep without letting an accidental load test escalate the bill."
  type        = number
  default     = 4
}

variable "seconds_until_auto_pause" {
  description = "Aurora Serverless v2 idle window before scaling to `min_capacity_acu`. Only meaningful when `min_capacity_acu = 0`. Valid range 300 (5 min, AWS minimum) to 86400 (24 hours, AWS maximum). Default 300 for the cost-conscious dev story; first connection after pause incurs a ~15 sec cold start per AWS."
  type        = number
  default     = 300
}

variable "backup_retention_days" {
  description = "Number of days to retain automated backups. Aurora's minimum is 1 day; we keep 7 to give us a one-week recovery window for dev without paying for the longer prod-tier 30-day retention."
  type        = number
  default     = 7
}

variable "master_username" {
  description = "Master username for the Aurora cluster. Better-Auth's drizzle migrations run as this user; a service-scoped role created via post-apply SQL takes over for the auth service runtime."
  type        = string
  default     = "panakoes_auth"
}

variable "database_name" {
  description = "Default initial database created on cluster bootstrap. Better-Auth's `user` / `session` / `account` / `verification` tables land here."
  type        = string
  default     = "panakoes_auth"
}
