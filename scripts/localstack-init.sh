#!/usr/bin/env bash
# scripts/localstack-init.sh: Bootstrap LocalStack with all AWS resources
# that Panakoes services expect to find at startup.
#
# Idempotent: every create command uses "|| true" or a conditional check
# so re-running is safe. Resources already present are skipped silently.
#
# Usage:
#   scripts/localstack-init.sh
#
# Caller: scripts/dev-up.sh calls this automatically when DEV_LOCALSTACK=1.
# The caller is responsible for ensuring LocalStack is healthy before calling.
#
# Resource discovery: each section's resource names were read from the
# service config.py files under services/*/src/ and cross-referenced with
# the Terraform modules under infra/dev/.

set -euo pipefail

ENDPOINT="http://localhost:4566"
REGION="us-east-1"
ACCOUNT_ID="000000000000"

AWS_CMD=(aws --endpoint-url "${ENDPOINT}" --region "${REGION}")

# Dummy credentials for LocalStack (standard convention).
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION="${REGION}"

RESOURCE_COUNT=0

log() {
    echo "[localstack-init] $*"
}

# ---------------------------------------------------------------------------
# KMS key helper: create a KMS key and return its key ID.
# LocalStack accepts any key spec; we use a symmetric default.
# ---------------------------------------------------------------------------
create_kms_key_if_missing() {
    local alias_name="$1"
    local full_alias="alias/${alias_name}"
    local key_id

    # Check if the alias already exists.
    if "${AWS_CMD[@]}" kms describe-key --key-id "${full_alias}" >/dev/null 2>&1; then
        return 0
    fi

    key_id=$("${AWS_CMD[@]}" kms create-key \
        --description "LocalStack test key for ${alias_name}" \
        --query 'KeyMetadata.KeyId' --output text)

    "${AWS_CMD[@]}" kms create-alias \
        --alias-name "${full_alias}" \
        --target-key-id "${key_id}" >/dev/null

    RESOURCE_COUNT=$((RESOURCE_COUNT + 1))
}

# ---------------------------------------------------------------------------
# S3 bucket helper
# ---------------------------------------------------------------------------
create_s3_bucket_if_missing() {
    local bucket="$1"
    if "${AWS_CMD[@]}" s3api head-bucket --bucket "${bucket}" >/dev/null 2>&1; then
        log "S3 bucket '${bucket}' already exists; skipping."
        return 0
    fi
    "${AWS_CMD[@]}" s3api create-bucket --bucket "${bucket}" >/dev/null
    log "Created S3 bucket: ${bucket}"
    RESOURCE_COUNT=$((RESOURCE_COUNT + 1))
}

# ---------------------------------------------------------------------------
# SQS queue helper
# ---------------------------------------------------------------------------
create_sqs_queue_if_missing() {
    local queue_name="$1"
    # GetQueueUrl returns 0 if the queue exists, nonzero if not.
    if "${AWS_CMD[@]}" sqs get-queue-url --queue-name "${queue_name}" >/dev/null 2>&1; then
        log "SQS queue '${queue_name}' already exists; skipping."
        return 0
    fi
    "${AWS_CMD[@]}" sqs create-queue --queue-name "${queue_name}" >/dev/null
    log "Created SQS queue: ${queue_name}"
    RESOURCE_COUNT=$((RESOURCE_COUNT + 1))
}

# ---------------------------------------------------------------------------
# Secrets Manager secret helper
# ---------------------------------------------------------------------------
create_secret_if_missing() {
    local secret_name="$1"
    local secret_value="$2"
    if "${AWS_CMD[@]}" secretsmanager describe-secret --secret-id "${secret_name}" >/dev/null 2>&1; then
        log "Secret '${secret_name}' already exists; skipping."
        return 0
    fi
    "${AWS_CMD[@]}" secretsmanager create-secret \
        --name "${secret_name}" \
        --secret-string "${secret_value}" >/dev/null
    log "Created secret: ${secret_name}"
    RESOURCE_COUNT=$((RESOURCE_COUNT + 1))
}

# ---------------------------------------------------------------------------
# DynamoDB table helper. Accepts an arbitrary attribute-definitions and
# key-schema JSON so each table can declare the correct key structure.
# ---------------------------------------------------------------------------
create_ddb_table_if_missing() {
    local table_name="$1"
    shift
    # Remaining args are passed directly to create-table.
    if "${AWS_CMD[@]}" dynamodb describe-table --table-name "${table_name}" >/dev/null 2>&1; then
        log "DynamoDB table '${table_name}' already exists; skipping."
        return 0
    fi
    "${AWS_CMD[@]}" dynamodb create-table \
        --table-name "${table_name}" \
        --billing-mode PAY_PER_REQUEST \
        "$@" >/dev/null
    log "Created DynamoDB table: ${table_name}"
    RESOURCE_COUNT=$((RESOURCE_COUNT + 1))
}

# ---------------------------------------------------------------------------
# 1. KMS keys (LocalStack free-tier keys, no real key material)
# ---------------------------------------------------------------------------
log "--- KMS keys ---"

create_kms_key_if_missing "panakoes-dev-secrets"
create_kms_key_if_missing "panakoes-dev-audio-uploads"
create_kms_key_if_missing "panakoes-dev-transcripts"
create_kms_key_if_missing "panakoes-dev-log-archive"
create_kms_key_if_missing "panakoes-dev-events"

# ---------------------------------------------------------------------------
# 2. Secrets Manager secrets
#
# Names match infra/dev/secrets/main.tf local.secrets map.
# Values are test-only placeholders. The JWT signing secret matches the
# deterministic test fixture used in services/ingestion-api/tests/conftest.py:
#   TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
# ---------------------------------------------------------------------------
log "--- Secrets Manager ---"

create_secret_if_missing "panakoes-dev/jwt-signing-secret" \
    "unit-test-shared-secret-32bytes-min!!"

create_secret_if_missing "panakoes-dev/anthropic-api-key" \
    "sk-ant-test-placeholder-for-localstack"

create_secret_if_missing "panakoes-dev/groq-api-key" \
    "gsk_test_placeholder_for_localstack"

create_secret_if_missing "panakoes-dev/openai-api-key" \
    "sk-test-placeholder-for-localstack"

create_secret_if_missing "panakoes-dev/stripe-test-key" \
    "placeholder_for_localstack_not_a_real_key"

create_secret_if_missing "panakoes-dev/stripe-webhook-signing-secret" \
    "whsec_test_placeholder_for_localstack"

create_secret_if_missing "panakoes-dev/postgres-auth-db-password" \
    "panakoes"

create_secret_if_missing "panakoes-dev/database-url" \
    "postgresql://panakoes:panakoes@localhost:5432/panakoes"

create_secret_if_missing "panakoes-dev/ses-smtp-credentials" \
    '{"username":"test-smtp-user","password":"test-smtp-password"}'

# ---------------------------------------------------------------------------
# 3. S3 buckets
#
# Bucket names use fixed test suffixes (no random_id like Terraform does)
# so the names are predictable and services can reference them directly
# via env vars without reading Terraform outputs.
#
# Canonical env vars pointing at these buckets:
#   INGESTION_BUCKET=panakoes-audio-uploads (ingestion-api default)
#   S3_TRANSCRIPTS_BUCKET=panakoes-dev-transcripts (summarization default)
#   S3_SUMMARIES_BUCKET=panakoes-dev-summaries (summarization default)
# ---------------------------------------------------------------------------
log "--- S3 buckets ---"

create_s3_bucket_if_missing "panakoes-audio-uploads"
create_s3_bucket_if_missing "panakoes-dev-transcripts"
create_s3_bucket_if_missing "panakoes-dev-summaries"
create_s3_bucket_if_missing "panakoes-dev-log-archive"

# ---------------------------------------------------------------------------
# 4. SQS queues
#
# Queue names match infra/dev/events/main.tf with name_prefix=panakoes-dev.
# DLQs are created first so the live queues can reference them (even though
# LocalStack redrive policies are advisory in the free tier).
# ---------------------------------------------------------------------------
log "--- SQS queues ---"

# DLQs
create_sqs_queue_if_missing "panakoes-dev-audio-uploaded-dlq"
create_sqs_queue_if_missing "panakoes-dev-transcript-completed-dlq"
create_sqs_queue_if_missing "panakoes-dev-summary-completed-dlq"
create_sqs_queue_if_missing "panakoes-dev-notification-dlq"

# Live queues
create_sqs_queue_if_missing "panakoes-dev-audio-uploaded-queue"
create_sqs_queue_if_missing "panakoes-dev-transcript-completed-queue"
create_sqs_queue_if_missing "panakoes-dev-summary-completed-queue"
create_sqs_queue_if_missing "panakoes-dev-notification-queue"

# ---------------------------------------------------------------------------
# 5. DynamoDB tables
#
# Schemas match infra/dev/data/main.tf and infra/dev/admin-state/main.tf.
# GSIs are included where they exist in Terraform; LocalStack honours them
# for query routing even in the free tier.
# ---------------------------------------------------------------------------
log "--- DynamoDB tables ---"

# Table: panakoes-dev-ingestion (infra/dev/data/main.tf Table 1)
# pk=pk (S), sk=sk (S); GSI: IngestionIdIndex (ingestion_id), StatusIndex (status+sk)
create_ddb_table_if_missing "panakoes-dev-ingestion" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
        AttributeName=ingestion_id,AttributeType=S \
        AttributeName=status,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --global-secondary-indexes \
        '[
          {
            "IndexName": "IngestionIdIndex",
            "KeySchema": [{"AttributeName": "ingestion_id", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"}
          },
          {
            "IndexName": "StatusIndex",
            "KeySchema": [
              {"AttributeName": "status", "KeyType": "HASH"},
              {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "KEYS_ONLY"}
          }
        ]'

# Table: panakoes-audit-log (audit-lib config default; also panakoes-dev-audit-log)
# pk=pk (S), sk=sk (S); GSI: ActionIndex (action+sk), ActorIndex (actor_id+sk), Tier3ActionIndex (tier3_action+sk)
create_ddb_table_if_missing "panakoes-dev-audit-log" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
        AttributeName=action,AttributeType=S \
        AttributeName=actor_id,AttributeType=S \
        AttributeName=tier3_action,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --global-secondary-indexes \
        '[
          {
            "IndexName": "ActionIndex",
            "KeySchema": [
              {"AttributeName": "action", "KeyType": "HASH"},
              {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
          },
          {
            "IndexName": "ActorIndex",
            "KeySchema": [
              {"AttributeName": "actor_id", "KeyType": "HASH"},
              {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
          },
          {
            "IndexName": "Tier3ActionIndex",
            "KeySchema": [
              {"AttributeName": "tier3_action", "KeyType": "HASH"},
              {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
          }
        ]'

# Also create the audit-lib default name (audit-lib config.py: table_name="panakoes-audit-log")
create_ddb_table_if_missing "panakoes-audit-log" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE

# Table: panakoes-dev-tenants (infra/dev/data/main.tf Table 3)
# pk=tenant_id (S)
create_ddb_table_if_missing "panakoes-dev-tenants" \
    --attribute-definitions \
        AttributeName=tenant_id,AttributeType=S \
    --key-schema \
        AttributeName=tenant_id,KeyType=HASH

# Table: panakoes-dev-api-keys (infra/dev/data/main.tf Table 4)
# pk=api_key_id (S)
create_ddb_table_if_missing "panakoes-dev-api-keys" \
    --attribute-definitions \
        AttributeName=api_key_id,AttributeType=S \
    --key-schema \
        AttributeName=api_key_id,KeyType=HASH

# Table: panakoes-dev-subscriptions (infra/dev/data/main.tf Table 6)
# pk=tenant_id (S), sk=subscription_id (S)
create_ddb_table_if_missing "panakoes-dev-subscriptions" \
    --attribute-definitions \
        AttributeName=tenant_id,AttributeType=S \
        AttributeName=subscription_id,AttributeType=S \
    --key-schema \
        AttributeName=tenant_id,KeyType=HASH \
        AttributeName=subscription_id,KeyType=RANGE

# Table: panakoes-dev-streaming-sessions (infra/dev/data/main.tf Table 5)
# pk=session_id (S); GSI: UserSessionsIndex (user_id+created_at), ActiveSessionsIndex (status+created_at)
create_ddb_table_if_missing "panakoes-dev-streaming-sessions" \
    --attribute-definitions \
        AttributeName=session_id,AttributeType=S \
        AttributeName=user_id,AttributeType=S \
        AttributeName=status,AttributeType=S \
        AttributeName=created_at,AttributeType=S \
    --key-schema \
        AttributeName=session_id,KeyType=HASH \
    --global-secondary-indexes \
        '[
          {
            "IndexName": "UserSessionsIndex",
            "KeySchema": [
              {"AttributeName": "user_id", "KeyType": "HASH"},
              {"AttributeName": "created_at", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
          },
          {
            "IndexName": "ActiveSessionsIndex",
            "KeySchema": [
              {"AttributeName": "status", "KeyType": "HASH"},
              {"AttributeName": "created_at", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "KEYS_ONLY"}
          }
        ]'

# Also create the session-manager default name (panakoes-streaming-sessions)
create_ddb_table_if_missing "panakoes-streaming-sessions" \
    --attribute-definitions \
        AttributeName=session_id,AttributeType=S \
    --key-schema \
        AttributeName=session_id,KeyType=HASH

# Table: panakoes-dev-billing-events (billing service: ddb_billing_table)
# Schema: pk (S) as a generic event-id partition key; billing service writes individual event rows.
create_ddb_table_if_missing "panakoes-dev-billing-events" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE

# Table: panakoes-dev-summaries (summarization: ddb_summaries_table; also query-api)
create_ddb_table_if_missing "panakoes-dev-summaries" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE

# Table: panakoes-notification (notification service default)
create_ddb_table_if_missing "panakoes-notification" \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE

# Table: panakoes-dev-cost-cache (admin-state/main.tf Table 1; cost-api)
# pk=cache_key (S)
create_ddb_table_if_missing "panakoes-dev-cost-cache" \
    --attribute-definitions \
        AttributeName=cache_key,AttributeType=S \
    --key-schema \
        AttributeName=cache_key,KeyType=HASH

# Table: panakoes-dev-tenant-cost-rollup (admin-state/main.tf Table 2; cost-api)
# pk=tenant_id (S), sk=day_service (S)
create_ddb_table_if_missing "panakoes-dev-tenant-cost-rollup" \
    --attribute-definitions \
        AttributeName=tenant_id,AttributeType=S \
        AttributeName=day_service,AttributeType=S \
    --key-schema \
        AttributeName=tenant_id,KeyType=HASH \
        AttributeName=day_service,KeyType=RANGE

# Table: panakoes-dev-lifecycle-state (admin-state/main.tf Table 3; admin-api)
# pk=idempotency_key (S)
create_ddb_table_if_missing "panakoes-dev-lifecycle-state" \
    --attribute-definitions \
        AttributeName=idempotency_key,AttributeType=S \
    --key-schema \
        AttributeName=idempotency_key,KeyType=HASH

# Table: panakoes-dev-alert-state (admin-state/main.tf Table 4; cost-api)
# pk=alert_signature (S)
create_ddb_table_if_missing "panakoes-dev-alert-state" \
    --attribute-definitions \
        AttributeName=alert_signature,AttributeType=S \
    --key-schema \
        AttributeName=alert_signature,KeyType=HASH

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "---"
log "localstack-init: created ${RESOURCE_COUNT} resources."
log "Endpoint: ${ENDPOINT} | Region: ${REGION}"
log "Services ready: S3, SQS, DynamoDB, Secrets Manager, KMS"
