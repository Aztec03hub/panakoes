locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "admin-state"
  }
}

# ---------------------------------------------------------------------------
# Encryption note
#
# Same trade-off as `infra/dev/data/`: AWS-managed DynamoDB encryption
# instead of a customer managed CMK. The data here is operational
# state (cached cost numbers, lifecycle idempotency keys, alert
# dedup signatures, per-tenant rollups). None of it is regulated
# PII or payment data. CMK fees would not buy us a meaningful
# threat-model improvement at this stage. Flip to CMK if the threat
# model later needs "an internal AWS-side actor with DynamoDB read
# access could see plaintext" coverage.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Table 1: panakoes-dev-cost-cache
#
# Backs the cost-api service (Tier 2 of the admin dashboard). Caches
# results from AWS Cost Explorer queries so the dashboard's
# by-service / by-tenant / forecast views can render in tens of
# milliseconds instead of paying the 1-3 second CE round trip and
# the $0.01-per-query CE fee on every page load.
#
#   pk = cache_key (string composed by the service: e.g.
#                   "service-cost:2026-05-08:granularity=DAILY")
#
# TTL on `expires_at` (Unix epoch seconds). Cost-api writes this on
# every cache fill with a horizon proportional to the granularity
# (raw daily numbers cache for ~6 hours; forecasts and anomaly
# baselines cache for shorter windows). DynamoDB TTL deletion is
# free and asynchronous (within ~48h of the timestamp), which is
# fine for a cache where stale-but-recent data is acceptable.
#
# No GSIs. The only access pattern is "look up a single cache_key
# directly", which a primary-key Get satisfies. Adding a GSI now
# would just be ceremony.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "cost_cache" {
  name         = "${var.project_name}-${var.environment}-cost-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Table 2: panakoes-dev-tenant-cost-rollup
#
# Backs the Tier 2.2 per-tenant cost view. Holds pre-aggregated
# daily cost numbers per tenant so the dashboard can render
# "top tenants by cost this month" without scanning audit + usage
# data on every request.
#
#   pk = tenant_id
#   sk = day (YYYY-MM-DD)
#
# Composite key models the "list a tenant's days in chronological
# range" pattern as a bounded Query. Day-level granularity is the
# right floor: hourly rollups would explode the row count, monthly
# rollups would lose the resolution we need for anomaly detection.
#
# A nightly batch job in cost-api populates this table from the raw
# CE numbers + tenant-attribution tags. The aggregation logic lives
# in Phase 2 of the implementation plan; this Terraform just makes
# the table exist.
#
# No TTL: rollup history retains for the lifetime of the tenant.
# Deletion is handled by an explicit Tier 3 lifecycle operation
# ("purge tenant data") rather than a TTL sweep.
#
# No GSIs in Phase 0. Phase 2 will likely add an index keyed on
# `day` so "rank tenants by cost on day X" can run as a Query, but
# it costs nothing to defer that decision until the access pattern
# is exercised against real data.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "tenant_cost_rollup" {
  name         = "${var.project_name}-${var.environment}-tenant-cost-rollup"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "tenant_id"
  range_key    = "day"

  attribute {
    name = "tenant_id"
    type = "S"
  }

  attribute {
    name = "day"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Table 3: panakoes-dev-lifecycle-state
#
# Backs the Tier 3 admin-api safety pattern. Every lifecycle
# operation (terminate session, purge tenant data, revoke API
# credentials, etc.) writes a row here keyed on the operation's
# idempotency key. The row holds the full operation envelope:
# the typed-confirmation string the caller submitted, the result
# of the audit-before write, the authorization context, the
# operation's terminal status, and a result envelope.
#
#   pk = idempotency_key (UUID provided by the caller)
#
# Two access patterns:
#   1. Pre-flight idempotency check: GetItem on a fresh request to
#      detect "this exact operation already ran"; returns the cached
#      result if so. Duplicates collapse to a single side-effect.
#   2. Post-flight result fetch: the SvelteKit admin frontend polls
#      GetItem on the same key while a long operation runs.
#
# TTL on `expires_at` cleans up rows 24 hours after operation
# completion. Idempotency windows longer than 24h are out of scope:
# anything longer becomes a new operation. The audit log holds the
# permanent record; this table is operational scratch space.
#
# No GSIs. The only access pattern is "fetch by idempotency key".
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "lifecycle_state" {
  name         = "${var.project_name}-${var.environment}-lifecycle-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "idempotency_key"

  attribute {
    name = "idempotency_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Table 4: panakoes-dev-alert-state
#
# Backs the Tier 2.2 anomaly detection pipeline. Cost-api's anomaly
# detector emits an alert when daily spend deviates from a tenant's
# baseline by more than the configured threshold. Without a dedup
# layer the same anomaly would fire repeatedly across the detector's
# polling interval; this table lets the detector ask "did we already
# emit an alert for this signature in the active window?" before
# emitting again.
#
#   pk = alert_signature (hash of the alert dimensions: detector
#                        name, tenant_id, dimension key, day window)
#
# TTL on `expires_at` clears the dedup row after the alert's quiet
# period ends. Default quiet period is 24h; configurable per
# detector. Once expired, the same anomaly signature will fire a
# fresh alert if it persists.
#
# No GSIs. The only access pattern is "is signature X currently
# suppressed?", which a primary-key Get satisfies.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "alert_state" {
  name         = "${var.project_name}-${var.environment}-alert-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alert_signature"

  attribute {
    name = "alert_signature"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = local.common_tags
}
