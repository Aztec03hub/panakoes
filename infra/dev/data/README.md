# Dev Environment Data Layer

Per-environment Terraform configuration creating the DynamoDB tables
backing the Ingestion API, the panakoes-audit library, and the
Session Manager Lambda for the `dev` environment. Consumes the S3
remote state backend created by `infra/bootstrap/`; state lives at
`dev/data/terraform.tfstate`.

## What this creates

Three DynamoDB tables, all on `PAY_PER_REQUEST` billing with
server-side encryption (AWS-managed key), point-in-time recovery, and
deletion protection enabled.

### `panakoes-dev-ingestion`

Used by the Ingestion API to persist ingestion intent records.

| Element | Value |
|---|---|
| Hash key | `pk` (string), composite `USER#{user_id}` |
| Range key | `sk` (string), composite `INGESTION#{ingestion_id}` |
| GSI `IngestionIdIndex` | hash `ingestion_id`, projection ALL |
| GSI `StatusIndex` | hash `status`, range `sk`, projection KEYS_ONLY |
| TTL | `expires_at` (Unix seconds), abandoned uploads expire after 30 days |

The composite `pk`/`sk` models the dominant access pattern ("list a
user's ingestions, newest first") as a bounded Query, not a scan.
`IngestionIdIndex` exists because callbacks and webhooks arrive with
only an `ingestion_id`; without the GSI we would need to scan the
table to resolve them. `StatusIndex` projects KEYS_ONLY because the
retry / monitoring worker only needs the keys to fetch the full item;
KEYS_ONLY keeps GSI storage cost minimal.

### `panakoes-dev-audit-log`

Used by the panakoes-audit library to record application-level audit
events. Schema mirrors `services/audit-lib/README.md`.

| Element | Value |
|---|---|
| Hash key | `pk` (string), composite `AUDIT#{source_service}#{actor_id}` |
| Range key | `sk` (string), composite `{timestamp_iso}#{request_id}` |
| GSI `ActionIndex` | hash `action`, range `sk`, projection ALL |
| GSI `ActorIndex` | hash `actor_id`, range `sk`, projection ALL |
| TTL | none (audit log retains forever) |

The `pk` includes both `source_service` and `actor_id` so the common
"audit trail for user X inside service Y" query is a Query, not a
scan. The ISO-8601 timestamp prefix on `sk` gives free chronological
ordering inside the partition. `ActionIndex` finds all events with a
given action across all actors and services (compliance and incident
review). `ActorIndex` finds the full audit trail for a single actor
across services (support workflows). Both GSIs project ALL because
audit consumers want the entire event payload; this trades GSI
storage for fewer round-trips on read.

No TTL is configured. Audit retention forever is the default; a
separate config will later attach a DynamoDB Stream and ship aged
events to S3 for long-term Athena-queryable archive.

### `panakoes-dev-streaming-sessions`

Used by the Session Manager Lambda to track live transcription
session state (status, owner, lifecycle timestamps, GPU instance
metadata).

| Element | Value |
|---|---|
| Hash key | `session_id` (string) |
| GSI `UserSessionsIndex` | hash `user_id`, range `created_at`, projection ALL |
| GSI `ActiveSessionsIndex` | hash `status`, range `created_at`, projection KEYS_ONLY |
| TTL | `expires_at` (Unix seconds), 24h after session end |

Single-key access is the dominant pattern ("look up session by id").
`UserSessionsIndex` powers the dashboard's "my recent sessions" view;
projection ALL avoids follow-up GetItems for each row rendered.
`ActiveSessionsIndex` enumerates active or starting sessions for the
idle-timeout reaper Lambda; KEYS_ONLY is enough because the reaper
only needs `session_id` to terminate the underlying GPU instance.

## Why PAY_PER_REQUEST

At dev volumes (single-user testing, occasional integration runs)
provisioned capacity is overkill and over-priced. PAY_PER_REQUEST has
no minimum capacity charge and DynamoDB auto-scales transparently.
Cost at this scale is pennies per month per table for storage and
on-demand requests. PITR adds approximately $0.20 per GB-month per
table; for tables sitting at megabytes, that is also pennies.

## Why no customer-managed KMS key

The data is operational (ingestion intent, audit events, ephemeral
session state), access already requires IAM authentication, and a
CMK adds about $1/month per key plus per-request KMS charges. We
flip to a CMK if the threat model later includes "internal AWS-side
read access" as a concern.

## Apply

    cd infra/dev/data
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and initializes the S3
backend (talks to the bucket created by `infra/bootstrap/`).

## Consuming outputs from other configs

Downstream configurations (Ingestion API, audit-lib consumers,
Session Manager Lambda IAM policies) read the table names and ARNs
via a `terraform_remote_state` data source pointing at the same
backend bucket and the `dev/data/terraform.tfstate` key:

    data "terraform_remote_state" "data" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/data/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.data.outputs.ingestion_table_arn
    #   data.terraform_remote_state.data.outputs.audit_log_table_name
    #   data.terraform_remote_state.data.outputs.streaming_sessions_table_arn

GSI ARNs are not exposed as outputs; downstream IAM policies derive
them from the table ARN with `"${arn}/index/*"`.

## Outputs

| Output                              | Type   | Purpose                                  |
|-------------------------------------|--------|------------------------------------------|
| `ingestion_table_name`              | string | Name of the ingestion table              |
| `ingestion_table_arn`               | string | ARN of the ingestion table               |
| `audit_log_table_name`              | string | Name of the audit log table              |
| `audit_log_table_arn`               | string | ARN of the audit log table               |
| `streaming_sessions_table_name`     | string | Name of the streaming sessions table     |
| `streaming_sessions_table_arn`      | string | ARN of the streaming sessions table      |
