locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "streaming-frame-queues"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  pool_ids             = toset([for i in range(var.pool_size) : tostring(i)])
  app_data_kms_key_arn = data.terraform_remote_state.kms.outputs.app_data_key_arn
}

# ===========================================================================
# 32-slot SQS frame-queue pool
#
# One standard SQS queue per slot, named
# `panakoes-dev-stream-frames-pool-{0..N-1}`. Each session claims one
# queue via the DDB pool-state table below at spawn time, the GPU
# container consumes frames from it, and the lifecycle reaper releases
# the slot on session end. PurgeQueue is never called; the next
# claimant's drain-then-claim handles residual messages.
#
# Standard queues (not FIFO) because per-session ordering is preserved
# by virtue of each session owning exactly one queue at a time. FIFO
# would add throughput caps and message-group-id complexity for no win.
# ===========================================================================

resource "aws_sqs_queue" "pool" {
  for_each = local.pool_ids

  name                       = "${local.name_prefix}-stream-frames-pool-${each.key}"
  visibility_timeout_seconds = 30   # frames are short-lived; redeliver fast if a consumer dies
  message_retention_seconds  = 3600 # 1 hour; far longer than any live session needs

  kms_master_key_id = local.app_data_kms_key_arn

  tags = merge(local.common_tags, {
    PoolSlot = each.key
  })
}

# ===========================================================================
# DDB pool-state table (HIGH-06 fix: ONE row per queue)
#
# Primary key `pool_queue_id` (numeric 0..N-1). Attributes:
#   - queue_url (string, required): the SQS URL for that slot.
#   - claimed_by (string, optional): session_id of the current owner.
#   - claimed_at (string, optional): ISO timestamp of the claim.
#
# Initial row population happens via `aws_dynamodb_table_item` so each
# row carries the matching `queue_url` from the for_each above without
# a separate bootstrap step.
# ===========================================================================

resource "aws_dynamodb_table" "frame_pool" {
  name         = "${local.name_prefix}-stream-frame-pool"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pool_queue_id"

  attribute {
    name = "pool_queue_id"
    type = "N"
  }

  point_in_time_recovery {
    enabled = false # ephemeral runtime state; PITR is overkill and adds cost.
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = false

  tags = local.common_tags
}

resource "aws_dynamodb_table_item" "pool_seed" {
  for_each = local.pool_ids

  table_name = aws_dynamodb_table.frame_pool.name
  hash_key   = aws_dynamodb_table.frame_pool.hash_key

  # JSON shape matches DDB's wire format. `pool_queue_id` is a number
  # (N), `queue_url` is a string (S). `claimed_by` is intentionally
  # absent so the gpu-spawner's `attribute_not_exists(claimed_by)`
  # conditional update succeeds on the first claim.
  item = jsonencode({
    pool_queue_id = { N = each.key }
    queue_url     = { S = aws_sqs_queue.pool[each.key].id }
  })

  # `aws_dynamodb_table_item` rewrites the row on every apply; we want
  # to leave the live `claimed_by` field untouched. lifecycle blocks
  # do not yet support ignoring nested JSON attributes, so we accept
  # the rewrite trade-off: in practice this only fires when the
  # `queue_url` for a slot changes (never, in steady state).
  lifecycle {
    ignore_changes = [item]
  }
}
