variable "aws_region" {
  description = "AWS region for the dev environment auth-db-rds instance."
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
  description = "RDS PostgreSQL engine version. Pinned to a stable 16.x minor. Bump deliberately; minor-version bumps are an in-place upgrade via `apply_immediately`."
  type        = string
  default     = "16.4"
}

variable "instance_class" {
  description = "RDS DB instance class. `db.t4g.micro` (Graviton 2 vCPU, 1 GB RAM) is the smallest current-gen Postgres instance and is included in the AWS Free Tier for 12 months from account creation (750 hours/month, single-AZ). For dev's tiny auth workload this is right-sized; production should override to a larger class once the workload demands it."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "RDS storage in GiB. 20 GiB is the AWS Free Tier maximum for the 12-month tier. Better-Auth's tables (`user`, `session`, `account`, `verification`) at dev scale fit in well under 1 GiB; the 20 GiB is headroom not currently consumed."
  type        = number
  default     = 20
}

variable "storage_type" {
  description = "RDS storage type. `gp3` is the current-generation SSD with predictable IOPS at no extra cost vs `gp2`. Free Tier covers gp2/gp3 equally."
  type        = string
  default     = "gp3"
}

variable "backup_retention_days" {
  description = "Number of days to retain automated backups. RDS minimum 1, maximum 35. 7 matches the Aurora module rationale: a one-week recovery window for dev without paying for prod-tier 30-day retention. Free Tier covers backup storage up to the size of the DB."
  type        = number
  default     = 7
}

variable "master_username" {
  description = "Master username for the RDS instance. Better-Auth's drizzle migrations run as this user; a service-scoped role created via post-apply SQL takes over for the auth service runtime."
  type        = string
  default     = "panakoes_auth"
}

variable "database_name" {
  description = "Default initial database created on instance bootstrap. Better-Auth's `user` / `session` / `account` / `verification` tables land here."
  type        = string
  default     = "panakoes_auth"
}
