locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "data"
  }
}

# ---------------------------------------------------------------------------
# Encryption note
#
# All three tables enable server-side encryption with the AWS-managed
# DynamoDB key (the default when `kms_key_arn` is omitted from the
# `server_side_encryption` block). We deliberately do not use a customer
# managed KMS key here. Trade-off:
#
#   - CMK adds ~$1/month per key plus per-request KMS charges that
#     compound with DynamoDB read/write volume.
#   - The data in these tables is operational state (ingestion intent,
#     audit events, ephemeral session metadata) and not regulated PII
#     or payment data.
#   - The tables sit inside a private AWS account; access already
#     requires IAM authentication.
#
# If the threat model later includes a "an internal AWS-side actor with
# DynamoDB read access could see plaintext" scenario, flip these to a
# CMK at that point.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Table 1: panakoes-dev-ingestion
#
# Backs the Ingestion API. One row per ingestion intent record. Composite
# key models the "list a user's ingestions" access pattern as a single
# DynamoDB Query without a scan.
#
#   pk = "USER#" + user_id
#   sk = "INGESTION#" + ingestion_id
#
# GSIs:
#   - IngestionIdIndex: lets services with only an ingestion_id (webhooks,
#     status callbacks) fetch the row directly without knowing user_id.
#     Projection ALL because callers typically want the full record.
#   - StatusIndex: scans pending / processing rows across users for retry
#     and monitoring jobs. Projection KEYS_ONLY because the worker only
#     needs the keys to fetch the full item afterwards; KEYS_ONLY keeps
#     index storage cost minimal at the cost of a follow-up GetItem.
#
# TTL on `expires_at` (Unix seconds). Not all rows set it; only abandoned
# uploads do, with a 30-day horizon. DynamoDB TTL is best-effort and free.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "ingestion" {
  name         = "${var.project_name}-${var.environment}-ingestion"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "ingestion_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "IngestionIdIndex"
    hash_key        = "ingestion_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "StatusIndex"
    hash_key        = "status"
    range_key       = "sk"
    projection_type = "KEYS_ONLY"
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
# Table 2: panakoes-dev-audit-log
#
# Backs the panakoes-audit library. Schema mirrors the README in
# services/audit-lib/.
#
#   pk = "AUDIT#" + source_service + "#" + actor_id
#   sk = timestamp_iso + "#" + request_id
#
# The composite pk groups events per (service, actor) pair so the
# common "show me everything user X did inside service Y" query is a
# bounded Query, not a scan. The sk's ISO-8601 timestamp prefix gives
# free chronological ordering.
#
# GSIs:
#   - ActionIndex: find all events with a given action across actors and
#     services. Range key sk gives chronological ordering inside the
#     partition. Projection defaults to ALL (omitted projection_type
#     attribute uses "ALL" implicitly via Terraform default), so audit
#     queries return the full event payload without a follow-up GetItem.
#   - ActorIndex: full audit trail for a single actor across all
#     services. Useful for support workflows and compliance exports.
#
# No TTL: audit events retain forever. A separate config will later
# attach a DynamoDB Stream and ship aged events to S3 for long-term
# Athena-queryable archive, but that is out of scope here.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "audit_log" {
  name         = "${var.project_name}-${var.environment}-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "action"
    type = "S"
  }

  attribute {
    name = "actor_id"
    type = "S"
  }

  attribute {
    name = "tier3_action"
    type = "S"
  }

  global_secondary_index {
    name            = "ActionIndex"
    hash_key        = "action"
    range_key       = "sk"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "ActorIndex"
    hash_key        = "actor_id"
    range_key       = "sk"
    projection_type = "ALL"
  }

  # Tier3ActionIndex: filter the audit log to ONLY Tier 3 admin
  # lifecycle operations (terminate session, purge tenant data,
  # revoke credentials, etc.) for the Tier 3.3 audit-log read view.
  # admin-api populates the `tier3_action` attribute on every
  # lifecycle operation it writes; ordinary audit events from other
  # services do not set the attribute, and DynamoDB's sparse-GSI
  # semantics keep them out of this index automatically. Range key
  # `sk` (timestamp + request_id) gives free chronological ordering
  # inside the partition. Projection ALL so the read view can render
  # the full operation envelope without follow-up GetItems.
  global_secondary_index {
    name            = "Tier3ActionIndex"
    hash_key        = "tier3_action"
    range_key       = "sk"
    projection_type = "ALL"
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
# Table 3: panakoes-dev-tenants
#
# Backs admin-api Tier 3 lifecycle ops (suspend / reactivate / purge
# tenant data) and any future per-tenant state lookups. v0.1 access
# pattern is point-lookup by tenant_id; no GSIs needed yet.
#
#   pk = tenant_id
#
# When list-by-status (e.g. "find all suspended tenants") becomes a
# real access pattern, add a sparse StatusIndex GSI in a follow-up.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "tenants" {
  name         = "${var.project_name}-${var.environment}-tenants"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "tenant_id"

  attribute {
    name = "tenant_id"
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
# Table 4: panakoes-dev-api-keys
#
# Backs admin-api Tier 3 lifecycle ops (revoke api key) and the
# auth path's per-key validation. v0.1 access pattern is point-lookup
# by api_key_id; the lifecycle revoke op only needs UpdateItem by id.
# When list-by-tenant ("show me all api keys for tenant X") becomes a
# real access pattern, add a TenantKeysIndex GSI in a follow-up.
#
#   pk = api_key_id
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "api_keys" {
  name         = "${var.project_name}-${var.environment}-api-keys"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "api_key_id"

  attribute {
    name = "api_key_id"
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
# Table 5: panakoes-dev-streaming-sessions
#
# Backs the Session Manager Lambda. Holds live transcription session
# state (status, owner, lifecycle timestamps, GPU instance metadata).
# The single hash key access pattern is "fetch session by id"; the
# secondary patterns are "list a user's sessions" and "find all active
# sessions for the reaper".
#
#   pk = session_id (UUID-ish)
#
# GSIs:
#   - UserSessionsIndex: list a user's sessions by created_at order.
#     Projection ALL so the dashboard can render rows without follow-up
#     GetItems.
#   - ActiveSessionsIndex: enumerate sessions in the "active" /
#     "starting" status for the idle-timeout reaper Lambda. Projection
#     KEYS_ONLY because the reaper just needs the session_id to act.
#
# TTL on `expires_at` cleans up rows 24 hours after the session ended.
# DynamoDB TTL deletion is free and asynchronous (within ~48h of the
# expiry timestamp), which is fine for this use case.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Table 6: panakoes-dev-subscriptions
#
# Backs the Billing service's current-state view of every Stripe-managed
# subscription. The event log (`panakoes-dev-billing-events`) is the
# append-only audit trail Stripe produces; this table is the materialised
# view downstream services consult to answer "what plan is tenant X on,
# right now?" in a single GetItem. The Auth service reads it at sign-in
# time and bakes the resulting `plan` claim into the issued JWT, so the
# read pattern is bounded Query-by-tenant.
#
#   pk = tenant_id        (Panakoes user / tenant id)
#   sk = subscription_id  (Stripe `sub_...` id; one row per subscription)
#
# Attributes: `plan` (free|pro|team), `status` (Stripe subscription
# status), `current_period_end` (ISO 8601), `cancel_at` (ISO 8601,
# optional), `quantity` (int), `stripe_customer_id` (optional),
# `last_event_id` (Stripe `evt_...` id of the last processed event,
# used by the billing webhook for conditional-PutItem idempotency),
# `updated_at` (ISO 8601 of the last write).
#
# No GSIs in v0.1: the only access patterns are "get plan for tenant"
# (Query by pk) and "upsert by (tenant, subscription)" (PutItem).
# Server-side encryption uses the AWS-managed DynamoDB key for the
# same trade-off documented at the top of this file.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "subscriptions" {
  name         = "${var.project_name}-${var.environment}-subscriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "tenant_id"
  range_key    = "subscription_id"

  attribute {
    name = "tenant_id"
    type = "S"
  }

  attribute {
    name = "subscription_id"
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

resource "aws_dynamodb_table" "streaming_sessions" {
  name         = "${var.project_name}-${var.environment}-streaming-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "UserSessionsIndex"
    hash_key        = "user_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "ActiveSessionsIndex"
    hash_key        = "status"
    range_key       = "created_at"
    projection_type = "KEYS_ONLY"
  }

  # Stage 2 streaming addition (design doc MED-04 + round-4 NIT). The
  # streaming-router writes `ttl_epoch_seconds` at $connect with a
  # 2-hour default so orphaned `connecting` rows auto-prune; the
  # lifecycle reaper overwrites to the longer 7-day window on
  # legitimate disconnect. The attribute name changes from `expires_at`
  # to `ttl_epoch_seconds`; DynamoDB tolerates a TTL-attribute rename
  # in-place via a single API call (no rebuild) and the prior schema
  # was unused in production.
  ttl {
    attribute_name = "ttl_epoch_seconds"
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
# Table 7: panakoes-dev-billing-events
#
# Append-only audit trail of every Stripe webhook the Billing service
# processes. Schema mirrors services/billing/src/panakoes_billing/storage/
# dynamodb.py. The table is write-heavy (one row per webhook event) and
# read-heavy for the debug / audit surface; PAY_PER_REQUEST matches the
# bursty traffic pattern without over-provisioning.
#
#   pk = "USER#" + user_id   (partition key)
#   sk = "EVENT#" + ulid     (sort key; ULIDs are time-ordered, so a
#                              reverse scan yields events newest-first)
#
# No GSIs in v0.1: the only access patterns are "list a user's events"
# (Query by pk, ScanIndexForward=False) and "find the latest subscription
# event" (same Query + client-side filter on event_type). A projected
# materialized view is deferred to the next billing slice.
#
# Note: an SNS topic named `panakoes-dev-billing-events` also exists
# (infra/dev/events/main.tf). SNS and DynamoDB have separate namespaces
# in AWS; the identical name is intentional and valid. The IAM task role
# forward-reference ARN and the billing service config both hard-code
# this table name; do not rename either resource.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "billing_events" {
  name         = "${var.project_name}-${var.environment}-billing-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
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
