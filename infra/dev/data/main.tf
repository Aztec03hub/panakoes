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
# Table 3: panakoes-dev-streaming-sessions
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
