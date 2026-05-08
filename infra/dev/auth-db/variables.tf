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
  description = "Aurora Serverless v2 minimum capacity in Aurora Capacity Units. 0.5 is the floor Aurora supports and the right idle target for dev (about $0.06/hr at idle vs about $0.12/hr at 1 ACU)."
  type        = number
  default     = 0.5
}

variable "max_capacity_acu" {
  description = "Aurora Serverless v2 maximum capacity in Aurora Capacity Units. 4 ACU is enough headroom for any dev integration test sweep without letting an accidental load test escalate the bill."
  type        = number
  default     = 4
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
