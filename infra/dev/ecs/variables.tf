variable "aws_region" {
  description = "AWS region for the dev ECS cluster and supporting resources."
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

# ---------------------------------------------------------------------------
# Cluster controls
# ---------------------------------------------------------------------------

variable "container_insights" {
  description = "Enables CloudWatch Container Insights on the ECS cluster. Adds ~$0.50 per cluster per day plus per-metric costs but is the only way to get task-level CPU / memory / network observability without instrumenting every container. On for dev to validate the dashboards now; revisit cost in production once steady-state metric volume is known."
  type        = string
  default     = "enabled"

  validation {
    condition     = contains(["enabled", "disabled"], var.container_insights)
    error_message = "container_insights must be either 'enabled' or 'disabled'."
  }
}

# ---------------------------------------------------------------------------
# Auth service controls
#
# Defaults sized for dev. Production overrides bump cpu / memory and
# desired_count; the variable surface lets that happen without a
# module rewrite.
# ---------------------------------------------------------------------------

variable "auth_image_tag" {
  description = "Docker image tag for the auth service to deploy. The full image URI is constructed as `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-auth:<tag>`. Default `latest` is acceptable in dev where rollback is fast and the ECR repo is IMMUTABLE-tagged so `latest` is a moving alias to the most recent push, but production should pin to a digest or a SemVer tag."
  type        = string
  default     = "latest"
}

variable "auth_container_port" {
  description = "Port the auth container listens on inside the task. Hono's default in services/auth/src/config.ts is 8080 (overridable via PORT env var). Must match the value the application binds to or health checks fail."
  type        = number
  default     = 8080
}

variable "auth_desired_count" {
  description = "Desired number of auth tasks the ECS service maintains. 1 is correct for dev (single-task, single-AZ-bias acceptable for non-prod); production should run >=2 spread across AZs for HA. Auto scaling is not yet wired."
  type        = number
  default     = 1
}

variable "auth_cpu" {
  description = "Fargate CPU units for the auth task. 256 = 0.25 vCPU, the smallest Fargate slice; matches the dev workload (a Node.js HTTP server doing JWT signing + Postgres calls). Fargate enforces fixed CPU/memory pairs; 256 CPU permits 512 / 1024 / 2048 MB memory."
  type        = number
  default     = 256
}

variable "auth_memory" {
  description = "Fargate memory in MiB for the auth task. 512 MiB pairs with 256 CPU and is comfortable for a Node.js process; raise to 1024 if Drizzle / Better-Auth working set grows."
  type        = number
  default     = 512
}

variable "auth_log_level" {
  description = "Log level the auth service emits. Trace / debug are noisy; default `info` matches production posture."
  type        = string
  default     = "info"
}

variable "auth_node_env" {
  description = "NODE_ENV passed to the auth container. `production` enables Hono / Drizzle production-mode optimizations even in dev because the dev environment is not local development."
  type        = string
  default     = "production"
}

variable "auth_jwt_issuer" {
  description = "JWT `iss` claim value the auth service sets on issued tokens. Defaults to the planned production issuer URL; downstream services validating JWTs must use the same value. Override per-environment if a separate issuer URL is wired (e.g. `https://auth.dev.panakoes.com`)."
  type        = string
  default     = "https://auth.panakoes.com"
}

variable "auth_jwt_audience" {
  description = "JWT `aud` claim value the auth service sets on issued tokens. Downstream services validating JWTs must require the same value. `panakoes-api` matches the default in services/auth/src/config.ts."
  type        = string
  default     = "panakoes-api"
}

variable "auth_jwt_expires_in_seconds" {
  description = "Lifetime in seconds of issued JWTs. 3600 (1 hour) matches the application default; refresh tokens (when implemented) will live longer."
  type        = number
  default     = 3600
}

variable "auth_better_auth_url" {
  description = "BETTER_AUTH_URL value passed to the auth container. Better-Auth uses this as the canonical base URL when issuing redirect URLs / cookies. In dev this should resolve to the public API Gateway invoke URL fronting the auth routes; we leave it as the production placeholder until the api-gateway custom-domain wiring lands."
  type        = string
  default     = "https://auth.panakoes.com"
}

variable "auth_health_check_path" {
  description = "HTTP path the NLB target group's HTTP health check probes on each task. Auth service exposes `/health` returning `{status: 'ok'}` (services/auth/src/health/health.ts)."
  type        = string
  default     = "/health"
}

variable "auth_deregistration_delay_seconds" {
  description = "Time the NLB waits before fully deregistering a draining target. 30 seconds is the floor that still gives in-flight requests time to finish; AWS default is 300 which makes deploys feel slow. 30 matches the auth service's stateless request profile (no long-polling, no SSE)."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the auth service log group. 30 days matches the project default (per dev/observability/) and is sufficient for incident triage without bloating storage cost."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Account context (for ECR image URI construction)
#
# The container image URI is built as
#   <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>.
# We could derive the account ID from `data.aws_caller_identity.current`
# and the region from `data.aws_region.current` (and we do, for the
# IAM ARN constructions in main.tf). The variable here lets a future
# operator override the account if a cross-account image source is
# ever needed; default empty string falls through to the data source.
# ---------------------------------------------------------------------------

variable "ecr_account_id_override" {
  description = "Optional AWS account ID hosting the ECR repository for the auth image. Empty string (default) uses the current account derived via `data.aws_caller_identity`. Set this only if pulling the image from a different account."
  type        = string
  default     = ""
}
