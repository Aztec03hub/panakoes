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
  description = "Docker image tag for the auth service to deploy. The full image URI is constructed as `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-auth:<tag>`. Pinned to `migrate-8c90ace`, the build that bakes in the runtime `dist/migrate.js` migration runner shipped by PR #219. The earlier `c-plus-f670890` tag predates the migration runner and would silently roll back the auth task to an image that cannot run `scripts/run-auth-migration.sh`; do not regress to it. The `latest` tag is the pre-c+ image with `/auth/*` mount paths incompatible with the current api-gateway routing (ADR-038). Production should pin to a digest or a SemVer tag instead of a moving build identifier."
  type        = string
  default     = "migrate-8c90ace"
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
# cost-api service controls
#
# Tier 2 admin-dashboard backend. Defaults match the auth pattern
# (256 CPU / 512 MiB, 1 task, ARM64) plus a Python-uvicorn-default
# container port of 8000. Production overrides bump cpu / memory and
# desired_count without a module rewrite.
# ---------------------------------------------------------------------------

variable "cost_api_image_tag" {
  description = "Docker image tag for the cost-api service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-cost-api:<tag>`. Default `latest` is fine for dev (ECR repo is IMMUTABLE-tagged so `latest` is a moving alias to the most recent push); production should pin to a digest or SemVer tag."
  type        = string
  default     = "latest"
}

variable "cost_api_container_port" {
  description = "Port the cost-api container listens on. uvicorn default in services/cost-api/Dockerfile (CMD `--port 8000`). Must match the value the application binds to or NLB health checks fail."
  type        = number
  default     = 8000
}

variable "cost_api_desired_count" {
  description = "Desired number of cost-api tasks the ECS service maintains. 1 is correct for dev; production should run >=2 spread across AZs for HA."
  type        = number
  default     = 1
}

variable "cost_api_cpu" {
  description = "Fargate CPU units for the cost-api task. 256 = 0.25 vCPU, the smallest Fargate slice; matches the dev workload (a Python FastAPI process making periodic Cost Explorer + DynamoDB calls)."
  type        = number
  default     = 256
}

variable "cost_api_memory" {
  description = "Fargate memory in MiB for the cost-api task. 512 MiB pairs with 256 CPU and is comfortable for a FastAPI process; raise to 1024 if the in-process cache footprint grows."
  type        = number
  default     = 512
}

variable "cost_api_log_level" {
  description = "Log level the cost-api service emits. Maps to the `LOG_LEVEL` env var consumed by pydantic-settings. Default `INFO` matches production posture."
  type        = string
  default     = "INFO"
}

variable "cost_api_health_check_path" {
  description = "HTTP path the NLB target group probes on each cost-api task. The service exposes `/health` returning `{status: 'ok'}` (services/cost-api/src/panakoes_cost_api/main.py)."
  type        = string
  default     = "/health"
}

variable "cost_api_deregistration_delay_seconds" {
  description = "Seconds the NLB waits before fully deregistering a draining cost-api target. 30s gives in-flight requests time to finish without making deploys feel slow; matches the auth pattern."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# admin-api service controls
#
# Tier 3 admin-dashboard backend. Same default shape as cost-api
# (256 CPU / 512 MiB, 1 task, ARM64, port 8000).
# ---------------------------------------------------------------------------

variable "admin_api_image_tag" {
  description = "Docker image tag for the admin-api service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-admin-api:<tag>`. Default `latest` is fine for dev; production should pin to a digest or SemVer tag."
  type        = string
  default     = "latest"
}

variable "admin_api_container_port" {
  description = "Port the admin-api container listens on. uvicorn default in services/admin-api/Dockerfile (CMD `--port 8000`)."
  type        = number
  default     = 8000
}

variable "admin_api_desired_count" {
  description = "Desired number of admin-api tasks the ECS service maintains. 1 is correct for dev; production should run >=2 spread across AZs for HA."
  type        = number
  default     = 1
}

variable "admin_api_cpu" {
  description = "Fargate CPU units for the admin-api task. 256 = 0.25 vCPU; matches the dev workload (FastAPI process running lifecycle ops with low steady-state load)."
  type        = number
  default     = 256
}

variable "admin_api_memory" {
  description = "Fargate memory in MiB for the admin-api task. 512 MiB pairs with 256 CPU."
  type        = number
  default     = 512
}

variable "admin_api_log_level" {
  description = "Log level the admin-api service emits (LOG_LEVEL env var). Default `INFO` matches production posture."
  type        = string
  default     = "INFO"
}

variable "admin_api_health_check_path" {
  description = "HTTP path the NLB target group probes on each admin-api task. The service exposes `/health` (services/admin-api/src/panakoes_admin_api/main.py)."
  type        = string
  default     = "/health"
}

variable "admin_api_deregistration_delay_seconds" {
  description = "Seconds the NLB waits before fully deregistering a draining admin-api target. 30s matches the auth + cost-api pattern."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# ingestion-api service controls
#
# FastAPI service that issues pre-signed S3 PUT URLs for raw audio
# uploads and persists upload metadata to DynamoDB. Same default
# shape as cost-api / admin-api (256 CPU / 512 MiB, 1 task, ARM64,
# port 8000).
# ---------------------------------------------------------------------------

variable "ingestion_api_image_tag" {
  description = "Docker image tag for the ingestion-api service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-ingestion-api:<tag>`. Default `latest` is fine for dev; production should pin to a digest or SemVer tag."
  type        = string
  default     = "latest"
}

variable "ingestion_api_container_port" {
  description = "Port the ingestion-api container listens on. uvicorn default in services/ingestion-api/Dockerfile (CMD `--port 8000`)."
  type        = number
  default     = 8000
}

variable "ingestion_api_desired_count" {
  description = "Desired number of ingestion-api tasks the ECS service maintains. 1 is correct for dev; production should run >=2 spread across AZs for HA."
  type        = number
  default     = 1
}

variable "ingestion_api_cpu" {
  description = "Fargate CPU units for the ingestion-api task. 256 = 0.25 vCPU; matches the dev workload (FastAPI process issuing pre-signed URLs + DDB writes)."
  type        = number
  default     = 256
}

variable "ingestion_api_memory" {
  description = "Fargate memory in MiB for the ingestion-api task. 512 MiB pairs with 256 CPU."
  type        = number
  default     = 512
}

variable "ingestion_api_log_level" {
  description = "Log level the ingestion-api service emits (LOG_LEVEL env var). Default `INFO` matches production posture."
  type        = string
  default     = "INFO"
}

variable "ingestion_api_health_check_path" {
  description = "HTTP path the NLB target group probes on each ingestion-api task. The service exposes `/health` via the health router (services/ingestion-api/src/panakoes_ingestion_api/routes/health.py)."
  type        = string
  default     = "/health"
}

variable "ingestion_api_deregistration_delay_seconds" {
  description = "Seconds the NLB waits before fully deregistering a draining ingestion-api target. 30s matches the cost-api / admin-api pattern."
  type        = number
  default     = 30
}

variable "ingestion_api_presigned_url_ttl_seconds" {
  description = "Lifetime in seconds of pre-signed S3 PUT URLs the ingestion-api issues. 900 (15 minutes) matches the documented service contract in services/ingestion-api/src/panakoes_ingestion_api/config.py."
  type        = number
  default     = 900
}

# ---------------------------------------------------------------------------
# query-api service controls
#
# Read-only FastAPI surface across ingestion / summaries / sessions
# DDB tables. Same default shape as cost-api / admin-api (256 CPU /
# 512 MiB, 1 task, ARM64, port 8000).
# ---------------------------------------------------------------------------

variable "query_api_image_tag" {
  description = "Docker image tag for the query-api service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-query-api:<tag>`. Default `latest` is fine for dev; production should pin to a digest or SemVer tag."
  type        = string
  default     = "latest"
}

variable "query_api_container_port" {
  description = "Port the query-api container listens on. uvicorn default in services/query-api/Dockerfile (CMD `--port 8000`)."
  type        = number
  default     = 8000
}

variable "query_api_desired_count" {
  description = "Desired number of query-api tasks the ECS service maintains. 1 is correct for dev; production should run >=2 spread across AZs for HA."
  type        = number
  default     = 1
}

variable "query_api_cpu" {
  description = "Fargate CPU units for the query-api task. 256 = 0.25 vCPU; matches the dev workload (read-only FastAPI process making DDB Query / GetItem calls)."
  type        = number
  default     = 256
}

variable "query_api_memory" {
  description = "Fargate memory in MiB for the query-api task. 512 MiB pairs with 256 CPU."
  type        = number
  default     = 512
}

variable "query_api_log_level" {
  description = "Log level the query-api service emits (LOG_LEVEL env var). Default `INFO` matches production posture."
  type        = string
  default     = "INFO"
}

variable "query_api_health_check_path" {
  description = "HTTP path the NLB target group probes on each query-api task. The service exposes `/health` via the health router (services/query-api/src/panakoes_query_api/routes/health.py)."
  type        = string
  default     = "/health"
}

variable "query_api_deregistration_delay_seconds" {
  description = "Seconds the NLB waits before fully deregistering a draining query-api target. 30s matches the cost-api / admin-api pattern."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Image-tag pins for the 4 ECS services added in PR #229
# (summarization, notification, session-manager, billing).
#
# These variables are declared in PR #229's variables.tf; this PR pins the
# defaults to the freshly baked initial-<sha> tags so a future
# `terraform apply` on infra/dev/ecs picks the right images. When #229
# merges and this PR rebases on main, the variable blocks will already
# exist on main; resolve the conflict by keeping these pinned defaults.
# ---------------------------------------------------------------------------

variable "summarization_image_tag" {
  description = "Docker image tag for the summarization service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-summarization:<tag>`. Default `latest` is fine for dev; production should pin to a digest or SemVer tag."
  type        = string
  default     = "initial-90be43b"
}

variable "notification_image_tag" {
  description = "Docker image tag for the notification service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-notification:<tag>`. Default `latest` is fine for dev."
  type        = string
  default     = "initial-90be43b"
}

variable "session_manager_image_tag" {
  description = "Docker image tag for the session-manager service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-session-manager:<tag>`."
  type        = string
  default     = "initial-90be43b"
}

variable "billing_image_tag" {
  description = "Docker image tag for the billing service. Full URI: `<account>.dkr.ecr.<region>.amazonaws.com/panakoes-dev-billing:<tag>`."
  type        = string
  default     = "initial-90be43b"
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
