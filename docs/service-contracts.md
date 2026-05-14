# Service Contracts

Last updated: 2026-05-14
Generated from: actual source code survey of services/

## How to use this document

This document is the authoritative per-service boundary contract file for Panakoes. Read the relevant section(s) before touching any code that crosses service boundaries. Each section documents exact environment variable names (from config.py or config.ts), DynamoDB table names as read at runtime, S3 bucket names, SQS queues, inbound HTTP routes, and outbound calls. If a value is missing because the source could not be found, it is marked `(not found -- manual verification needed)`.

Load only the sections you need; the table of contents below gives anchor links.

---

## Table of contents

- [Ingestion API](#ingestion-api-servicesingestion-api)
- [Auth](#auth-servicesauth)
- [Admin API](#admin-api-servicesadmin-api)
- [Billing](#billing-servicesbilling)
- [Cost API](#cost-api-servicescost-api)
- [Health Aggregator](#health-aggregator-serviceshealth-aggregator)
- [Session Manager](#session-manager-servicessession-manager)
- [Query API](#query-api-servicesquery-api)
- [Notification](#notification-servicesnotification)
- [Summarization](#summarization-servicessummarization)
- [GPU Spawner](#gpu-spawner-servicesgpu-spawner)
- [Cost Rollup Aggregator](#cost-rollup-aggregator-servicescost-rollup-aggregator)
- [Event Router](#event-router-servicesevent-router)
- [Transcribe Worker](#transcribe-worker-servicestranscribe-worker)
- [Streaming Router](#streaming-router-servicesstreaming-router)
- [WS Authorizer](#ws-authorizer-servicesws-authorizer)
- [Transcriber Batch](#transcriber-batch-servicestranscriber-batch)
- [Transcriber Groq](#transcriber-groq-servicestranscriber-groq)
- [Transcriber Lib](#transcriber-lib-servicestranscriber-lib)
- [Audit Lib](#audit-lib-servicesaudit-lib)
- [Auth Client](#auth-client-servicesauth-client)
- [Middleware Lib](#middleware-lib-servicesmiddleware-lib)
- [Models Lib](#models-lib-servicesmodels-lib)
- [OTel Lib (Python)](#otel-lib-python-servicesotel-lib)
- [OTel Lib (TypeScript)](#otel-lib-typescript-servicesotel-lib-ts)

---

## Ingestion API (`services/ingestion-api/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-ingestion-api`
**API GW route prefix:** `/v1/ingestion-api/` (not confirmed -- manual verification needed against infra/dev/api-gateway)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `ingestion` | Service name used in OTel + log context |
| `LOG_LEVEL` | no | `INFO` | stdlib log level |
| `AWS_REGION` | no | `us-east-1` | AWS region for boto3 clients |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret for validating tokens signed by auth service |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer claim |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience claim |
| `INGESTION_TABLE_NAME` | no | `panakoes-ingestion` | DynamoDB table for ingestion records |
| `INGESTION_BUCKET` | no | `panakoes-audio-uploads` | S3 bucket for raw audio uploads |
| `PRESIGNED_URL_TTL_SECONDS` | no | `900` | Pre-signed PUT URL lifetime (15 min is the documented contract) |
| `LIST_DEFAULT_LIMIT` | no | `25` | Default page size for list endpoints |
| `LIST_MAX_LIMIT` | no | `100` | Maximum page size cap |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | Injected into OTel resource attributes |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Ingestion records | DynamoDB | value of `INGESTION_TABLE_NAME` (default: `panakoes-ingestion`) | read+write |
| Audio uploads | S3 | value of `INGESTION_BUCKET` (default: `panakoes-audio-uploads`) | generate pre-signed PUT URLs; read for transcription |
| Audit log | DynamoDB | via `panakoes_audit` lib (see `AUDIT_TABLE_NAME`) | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok", "service": "ingestion"}` |
| POST | `/ingestion/audio` | JWT Bearer | `{"filename": str, "content_type": str, "size_bytes": int}` | 201 `{"ingestion_id": str, "upload_url": str, "expires_at": datetime}` |
| GET | `/ingestion/{ingestion_id}` | JWT Bearer | -- | 200 `IngestionRecord` or 404 |
| GET | `/ingestion` | JWT Bearer | query: `limit` (int), `cursor` (str) | 200 `{"items": [...], "next_cursor": str\|null}` |
| POST | `/api/v1/transcribe/{ingestion_id}` | JWT Bearer | -- | 200 (already done), 202 (queued), 404 (not found) |

### Outbound calls to other Panakoes services

None (JWT validation is local using `JWT_SECRET`; transcription calls AWS Groq/transcriber backends directly).

### SQS messages produced

None directly. Transcription results are written back to DynamoDB.

### SQS messages consumed

None (the `transcribe-worker` Lambda reads SQS and calls back into ingestion-api's `transcribe_ingestion` function code directly by shared library import).

### DynamoDB tables

| Table env var | Default table name | Hash key | Range key | Notable GSIs |
|---|---|---|---|---|
| `INGESTION_TABLE_NAME` | `panakoes-ingestion` | `pk` (`USER#<user_id>`) | `sk` (`INGESTION#<ingestion_id>`) | (not found -- manual verification needed) |

### Notes

- Object key format in S3: `audio/{user_id}/{ingestion_id}/{filename}` -- this pattern is parsed by event-router and transcribe-worker.
- JWT validation uses the local `JWT_SECRET` (HS256); does NOT call the auth service's `/validate` endpoint per request.
- On-demand transcription route (`POST /api/v1/transcribe/{id}`) fires `transcribe_ingestion` as a FastAPI `BackgroundTask` and returns 202.
- The `panakoes_audit` lib is imported and `record_event` is called for `ingestion.intent_created` on upload.

---

## Auth (`services/auth/`)

**Runtime:** ECS Fargate
**Language:** TypeScript (Hono + Better-Auth + Drizzle ORM)
**Port:** 8080 (env var `PORT`, default 8080)
**ECR repo:** `panakoes-dev-auth`
**API GW route prefix:** `/v1/auth/`

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PORT` | no | `8080` | Port to listen on |
| `LOG_LEVEL` | no | `info` | pino log level (`trace`/`debug`/`info`/`warn`/`error`/`fatal`) |
| `NODE_ENV` | no | `development` | Runtime environment |
| `DATABASE_URL` | yes | -- | PostgreSQL connection string (Drizzle) |
| `AUTH_JWT_SECRET` | yes | -- | HS256 signing secret (min 32 bytes). Named `AUTH_JWT_SECRET` on the signer side; consuming services read it as `JWT_SECRET`. |
| `AUTH_JWT_ISSUER` | no | `https://auth.panakoes.com` | JWT `iss` claim |
| `AUTH_JWT_AUDIENCE` | no | `panakoes-api` | JWT `aud` claim |
| `AUTH_JWT_EXPIRES_IN_SECONDS` | no | `3600` | Token lifetime |
| `AUTH_JWT_ALGORITHM` | no | `HS256` | `HS256` (default) or `RS256` (opt-in, requires KMS) |
| `AUTH_JWT_KMS_KEY_ID` | no | -- | KMS key id/alias/ARN, required when `AUTH_JWT_ALGORITHM=RS256` |
| `AWS_REGION` | no | `us-east-1` | AWS region for KMS client |
| `BETTER_AUTH_URL` | no | `http://localhost:8080` | Better-Auth base URL |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| JWT signing key (RS256 mode) | AWS KMS | value of `AUTH_JWT_KMS_KEY_ID` | sign (asymmetric) |
| Postgres DB | RDS / Aurora | value of `DATABASE_URL` | read+write (users, sessions tables) |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok"}` |
| POST | `/sign-up` | none | `{"email": str, "password": str}` | 201 `{"token": str, "expiresAt": ISO, "user": {id, email, role}}` or 409 |
| POST | `/sign-in` | none | `{"email": str, "password": str}` | 200 `{"token": str, "expiresAt": ISO, "user": {id, email, role}}` or 401 |
| POST | `/sign-out` | JWT Bearer | -- | 204 |
| GET | `/me` | JWT Bearer | -- | 200 `{"user": {id, email, name, role}}` or 401 |
| POST | `/validate` | JWT Bearer | -- | 200 `{"valid": true, "user": {id, email, role}}` or 401 `{"valid": false, "reason": str}` |
| GET | `/.well-known/jwks.json` | none | -- | JWKS document (RS256 mode only) |
| POST/GET | `/api/auth/*` | varies | Better-Auth passthrough routes | Better-Auth managed |

### Outbound calls to other Panakoes services

None. Auth is a root dependency; it does not call other Panakoes services.

### DynamoDB tables

None. Auth uses PostgreSQL via Drizzle ORM.

### Notes

- Better-Auth manages the `user` and `session` tables in Postgres. Drizzle migrations live in `services/auth/drizzle/`.
- The JWT carries `sub` (user id), `email`, `role`, `jti` (session UUID), standard `iss`/`aud`/`exp` claims.
- CORS is hardcoded to `https://dmaopcm3hnxog.cloudfront.net`, `https://lafayettelabs.com`, `https://panakoes.com`, `http://localhost:5173`.
- After `0002_add_session_revoked_at.sql` added the `revokedAt` column, the service image must be rebaked after applying that migration (per CLAUDE.md runbook note).
- `/validate` checks revocation in the `session` table; services that need fresh revocation status should call this rather than validating the JWT signature alone.

---

## Admin API (`services/admin-api/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-admin-api`
**API GW route prefix:** `/v1/admin-api/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `admin-api` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `LIFECYCLE_STATE_TABLE` | no | `panakoes-dev-lifecycle-state` | Idempotency + state tracking for Tier 3 ops |
| `AUDIT_LOG_TABLE` | no | `panakoes-dev-audit-log` | Audit log for Tier 3 actions |
| `STREAMING_SESSIONS_TABLE` | no | `panakoes-dev-streaming-sessions` | Streaming sessions for terminate/block ops |
| `INGESTION_TABLE` | no | `panakoes-dev-ingestion` | Ingestion records for force-fail op |
| `TENANTS_TABLE` | no | `panakoes-dev-tenants` | Tenant records for block-tenant op |
| `API_KEYS_TABLE` | no | `panakoes-dev-api-keys` | API keys for revoke-api-key op |
| `EVENTS_BUS_NAME` | no | `panakoes-dev` | EventBridge bus for streaming-kill and billing-recompute events |
| `ENABLE_OPENAPI_DOCS` | no | `true` | Enable `/docs`, `/redoc`, `/openapi.json` (disable in prod) |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Lifecycle state | DynamoDB | `panakoes-dev-lifecycle-state` | read+write (idempotency) |
| Audit log | DynamoDB | `panakoes-dev-audit-log` | write (Tier 3 audit; also read via `/api/v1/admin/audit-log`) |
| Streaming sessions | DynamoDB | `panakoes-dev-streaming-sessions` | read+write (terminate/block-user ops) |
| Ingestion records | DynamoDB | `panakoes-dev-ingestion` | write (force-fail op) |
| Tenants | DynamoDB | `panakoes-dev-tenants` | write (block-tenant op) |
| API keys | DynamoDB | `panakoes-dev-api-keys` | write (revoke-api-key op) |
| EventBridge | EventBridge | bus name from `EVENTS_BUS_NAME` | publish (kill-streaming-session, force-billing-recompute) |
| AWS Batch | Batch | N/A | `terminate_job` API call (kill-batch-job op) |

### Inbound HTTP API

All lifecycle routes require admin JWT + step-up MFA token. Audit-read route requires admin JWT only.

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok", "service": "admin-api"}` |
| POST | `/api/v1/admin/sessions/{session_id}/terminate` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/ingestions/{ingestion_id}/force-fail` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/users/{user_id}/block-sessions` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/tenants/{tenant_id}/block` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/api-keys/{api_key_id}/revoke` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/streaming-sessions/{session_id}/kill` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/batch-jobs/{job_id}/kill` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| POST | `/api/v1/admin/tenants/{tenant_id}/force-billing-recompute` | admin JWT + step-up | `LifecycleRequest` | `LifecycleResponse` |
| GET | `/api/v1/admin/audit-log` | admin JWT | query: `limit`, `cursor`, `tier3_action` | paginated audit log |

### Notes

- The Tier 3 safety pattern: typed confirmation string + idempotency key + audit-before-AND-after + step-up MFA. Protocol failures (wrong confirmation, wrong state) return HTTP 200 with `status: "failed"` in the body; only transport/auth/validation failures return 4xx.
- `LifecycleRequest` includes an idempotency key and a confirmation string. The expected string for each op is documented in the route docstrings.
- `tenants_table` and `api_keys_table` were not provisioned in Terraform as of the initial Phase 2 commit; a follow-up PR is required.
- OpenAPI artifact at `services/admin-api/openapi.json` is the canonical schema for codegen regardless of the `ENABLE_OPENAPI_DOCS` toggle.

---

## Billing (`services/billing/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-billing`
**API GW route prefix:** `/v1/billing/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `billing` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience |
| `STRIPE_API_KEY` | yes | `sk_test_placeholder_replace_in_env` | Stripe API key; MUST start with `sk_test_` (live keys rejected at startup) |
| `STRIPE_WEBHOOK_SECRET` | yes (prod) | `whsec_placeholder_replace_in_env` | Stripe webhook signing secret |
| `STRIPE_PRICE_PRO` | no | `price_test_pro_placeholder` | Stripe Price ID for Pro tier |
| `STRIPE_PRICE_TEAM` | no | `price_test_team_placeholder` | Stripe Price ID for Team tier |
| `STRIPE_SUCCESS_URL` | no | `http://localhost:3000/billing/success` | Checkout success redirect |
| `STRIPE_CANCEL_URL` | no | `http://localhost:3000/billing/cancel` | Checkout cancel redirect |
| `STRIPE_PORTAL_RETURN_URL` | no | `http://localhost:3000/account` | Customer Portal return URL |
| `DDB_BILLING_TABLE` | no | `panakoes-dev-billing-events` | DynamoDB table for billing events |
| `DDB_SUBSCRIPTIONS_TABLE` | no | `panakoes-dev-subscriptions` | DynamoDB table for subscription state |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Billing events | DynamoDB | `panakoes-dev-billing-events` | read+write |
| Subscriptions state | DynamoDB | `panakoes-dev-subscriptions` | read+write |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok", "service": "billing"}` |
| POST | `/checkout-session` | JWT Bearer | `{"tier": "pro"\|"team", "seats": int\|null}` | 200 `{"checkout_url": str}` |
| POST | `/portal-session` | JWT Bearer | `{"return_url": str}` | 200 `{"url": str}` |
| POST | `/webhook` | Stripe-Signature header | raw Stripe payload | 200 `{"status": "ok"\|"ignored"\|"duplicate"}` |
| GET | `/subscription` | JWT Bearer | -- | 200 `{"tier": str\|null, "status": str\|null, "current_period_end": datetime\|null}` |

### Outbound calls to other Panakoes services

None directly. Calls Stripe API.

### Notes

- `/webhook` is intentionally unauthenticated at the JWT layer. Auth is the `Stripe-Signature` header verified against `STRIPE_WEBHOOK_SECRET`.
- The service refuses to boot if `STRIPE_API_KEY` does not start with `sk_test_` (validator in config.py). Live keys are permanently blocked.
- `return_url` for `/portal-session` is validated against a hardcoded allowlist: `https://dmaopcm3hnxog.cloudfront.net` and `https://panakoes.com`.
- Team tier minimum seats is 3 (`TEAM_MIN_SEATS = 3`); requests below this are rejected 422.
- Stripe events handled: `checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.paid`, `invoice.payment_failed`.
- Subscription state (`panakoes-dev-subscriptions`) is read by the auth service when minting plan claims in JWTs.

---

## Cost API (`services/cost-api/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-cost-api`
**API GW route prefix:** `/v1/cost-api/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `cost-api` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `COST_CACHE_TABLE` | no | `panakoes-dev-cost-cache` | DynamoDB cache table for CE results |
| `TENANT_COST_ROLLUP_TABLE` | no | `panakoes-dev-tenant-cost-rollup` | DynamoDB table with nightly per-tenant rollup rows |
| `ALERT_STATE_TABLE` | no | `panakoes-dev-alert-state` | DynamoDB table for cost anomaly alert dedup state |
| `AUDIT_LOG_TABLE` | no | `panakoes-dev-audit-log` | Audit log table (shared) |
| `COST_CACHE_TTL_SECONDS` | no | `3600` | Cache TTL for by-service results (1 hour) |
| `FORECAST_CACHE_TTL_SECONDS` | no | `21600` | Cache TTL for forecast results (6 hours) |
| `ANOMALY_CACHE_TTL_SECONDS` | no | `1800` | Cache TTL for anomaly results (30 min) |
| `CE_CONNECT_TIMEOUT_SECONDS` | no | `5` | boto3 CE client connect timeout |
| `CE_READ_TIMEOUT_SECONDS` | no | `15` | boto3 CE client read timeout (API Gateway integration timeout is 29s) |
| `CE_MAX_RETRY_ATTEMPTS` | no | `2` | boto3 CE client retry attempts |
| `ENABLE_OPENAPI_DOCS` | no | `true` | Enable `/docs`, `/redoc`, `/openapi.json` |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Cost cache | DynamoDB | `panakoes-dev-cost-cache` | read+write |
| Tenant cost rollup | DynamoDB | `panakoes-dev-tenant-cost-rollup` | read |
| Alert state | DynamoDB | `panakoes-dev-alert-state` | read |
| Cost Explorer | AWS CE API | N/A | read |

### Inbound HTTP API

All routes require admin JWT.

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok"}` |
| GET | `/api/v1/cost/by-service` | admin JWT | query: `from` (date), `to` (date) | `CostBreakdown` with `cache_hit` flag |
| GET | `/api/v1/cost/by-tenant` | admin JWT | query: `from` (date), `to` (date) | `TenantCostBreakdown` with per-service slices |
| GET | `/api/v1/cost/forecast` | admin JWT | query: `horizon_days` (7/14/30/60/90, default 30) | `CostForecast` with daily buckets + 95% prediction interval |
| GET | `/api/v1/cost/anomalies` | admin JWT | query: `detector` (str\|null), `active_only` (bool, default true), `kind` (reserved) | `CostAnomalyList` |

### Outbound calls to other Panakoes services

None. Reads AWS Cost Explorer directly.

### Notes

- CE calls are bounded by `ce_read_timeout_seconds` (15s). Timeouts map to 503; throttle maps to 502.
- The `by-tenant` route reads from `panakoes-dev-tenant-cost-rollup` which is populated by the `cost-rollup-aggregator` Lambda nightly at 02:00 UTC. An empty rollup returns `{"tenants": [], "total_cents": 0}` rather than 404.
- OpenAPI artifact at `services/cost-api/openapi.json` is the canonical schema for codegen.
- Forecast horizons allowed: 7, 14, 30, 60, 90 days. Other values return 400.
- `anomalies` route reads from `panakoes-dev-alert-state` for `active_only=true`; when `false`, also queries CE's anomaly detection API for the last 30 days.

---

## Health Aggregator (`services/health-aggregator/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-health-aggregator`
**API GW route prefix:** `/v1/health-aggregator/` (gateway strips prefix before forwarding)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `health-aggregator` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `ECS_CLUSTER` | no | `panakoes-dev` | ECS cluster name to query |
| `LOG_HEARTBEAT_WINDOW_SECONDS` | no | `300` | CloudWatch log recency window for heartbeat heuristic (5 min) |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| ECS service descriptions | ECS API | cluster `panakoes-dev` | read |
| ALB/NLB target group health | ELBv2 API | per-service TG ARNs from registry | read |
| CloudWatch log groups | CloudWatch Logs | `/panakoes/dev/<service>` | read (last log timestamp) |

### Inbound HTTP API

All routes require admin JWT.

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | `/health` | admin JWT | -- | `HealthSnapshot` (all services rolled up) |
| GET | `/services/{name}` | admin JWT | -- | `ServiceDetail` (single service with live metrics + logs) or 404 |

### Outbound calls to other Panakoes services

None. Queries AWS APIs directly.

### Notes

- Service registry is hardcoded in `services/health-aggregator/src/panakoes_health_aggregator/registry.py`. Monitored services: `auth`, `admin-api`, `cost-api`, `ingestion-api`, `summarization`, `notification`, `query-api`, `session-manager`, `gpu-spawner`, `transcriber-batch`, `transcriber-stream`, `event-router`, `billing`. ECS service names follow the pattern `panakoes-dev-<service>`.
- Services without a live ECS deployment report `"unknown / ecs service not found"` -- this is intentional.
- The API Gateway forwards `/v1/health-aggregator/{proxy+}` with the prefix stripped, so the SPA's `/v1/health-aggregator/health` reaches this router's `/health`.

---

## Session Manager (`services/session-manager/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-session-manager`
**API GW route prefix:** `/v1/session-manager/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `session-manager` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience |
| `SESSIONS_TABLE_NAME` | no | `panakoes-streaming-sessions` | DynamoDB table for streaming sessions |
| `SESSION_TTL_SECONDS` | no | `28800` | Session lifetime (8 hours); DynamoDB TTL attribute |
| `LIST_DEFAULT_LIMIT` | no | `25` | Default page size |
| `LIST_MAX_LIMIT` | no | `100` | Maximum page size cap |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Streaming sessions | DynamoDB | `panakoes-streaming-sessions` | read+write |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok", "service": "session-manager"}` |
| POST | `/sessions` | JWT Bearer | `{"language": str\|null}` | 201 `SessionRecord` |
| GET | `/sessions/{session_id}` | JWT Bearer | -- | 200 `SessionRecord` or 404 |
| GET | `/sessions` | JWT Bearer | query: `limit`, `cursor` | 200 `{"items": [...], "next_cursor": str\|null}` |
| PATCH | `/sessions/{session_id}` | JWT Bearer | `{"status": str\|null, "gpu_instance_id": str\|null}` | 200 `SessionRecord` or 404/409 |
| DELETE | `/sessions/{session_id}` | JWT Bearer | -- | 204 or 404 |

### Outbound calls to other Panakoes services

| Target service | How | Purpose |
|---|---|---|
| gpu-spawner | (not from this service directly; session-manager mints service-actor JWTs for the gpu-spawner) | See gpu-spawner notes |

### DynamoDB tables

| Table env var | Default table name | Hash key | Range key | Notable GSIs |
|---|---|---|---|---|
| `SESSIONS_TABLE_NAME` | `panakoes-streaming-sessions` | `pk` (`USER#<user_id>`) | `sk` (`SESSION#<session_id>`) | `UserSessionsIndex` on `user_id` (queried by admin-api block-user-sessions op) |

### Notes

- Session IDs use the format `sess_<ULID>`, which sorts lexicographically by creation time.
- State machine transitions: `starting -> active -> paused -> completed` / `starting -> errored`; validated against `ALLOWED_TRANSITIONS`.
- DELETE is a soft-delete: sets `status=errored` and `expires_at=now`; DynamoDB TTL sweeper removes the row within ~48 hours.
- Cross-user reads collapse to 404 (not 403) to avoid leaking record existence.
- Audit events: `session.created`, `session.status_changed`, `session.deleted`.

---

## Query API (`services/query-api/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-query-api`
**API GW route prefix:** `/v1/query-api/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `query` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience |
| `DDB_INGESTION_TABLE` | no | `panakoes-dev-ingestion` | DynamoDB table for ingestion records (read-only) |
| `DDB_SUMMARIES_TABLE` | no | `panakoes-dev-summaries` | DynamoDB table for summaries (read-only) |
| `DDB_SESSIONS_TABLE` | no | `panakoes-dev-streaming-sessions` | DynamoDB table for streaming sessions (read-only) |
| `LIST_DEFAULT_LIMIT` | no | `25` | Default page size |
| `LIST_MAX_LIMIT` | no | `100` | Maximum page size cap |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Ingestion records | DynamoDB | `panakoes-dev-ingestion` | read-only |
| Summaries | DynamoDB | `panakoes-dev-summaries` | read-only |
| Streaming sessions | DynamoDB | `panakoes-dev-streaming-sessions` | read-only |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok"}` |
| GET | `/ingestions` | JWT Bearer | query: `limit`, `cursor` | `IngestionListResponse` |
| GET | `/ingestions/{ingestion_id}` | JWT Bearer | -- | `IngestionRecord` or 404 |
| GET | `/sessions` | JWT Bearer | query: `limit`, `cursor` | `SessionListResponse` |
| GET | `/sessions/{session_id}` | JWT Bearer | -- | `SessionRecord` or 404 |
| GET | `/summaries` | JWT Bearer | query: `limit`, `cursor` | `SummaryListResponse` |
| GET | `/summaries/{transcript_id}` | JWT Bearer | -- | `SummaryRecord` or 404 |

### Notes

- Read-only service; it only queries DynamoDB tables owned by other services.
- All records are owner-scoped by JWT `sub`; cross-user access returns 404.
- Audit events emitted: `query.records_listed`, `query.record_fetched`.

---

## Notification (`services/notification/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-notification`
**API GW route prefix:** `/v1/notification/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `notification` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience |
| `SES_FROM_ADDRESS` | no | `no-reply@panakoes.com` | SES sender envelope address |
| `DDB_NOTIFICATION_TABLE` | no | `panakoes-notification` | DynamoDB table for notification records |
| `LIST_DEFAULT_LIMIT` | no | `25` | Default page size |
| `LIST_MAX_LIMIT` | no | `100` | Maximum page size cap |
| `WEBHOOK_MAX_ATTEMPTS` | no | `3` | Webhook delivery retry count |
| `WEBHOOK_BASE_BACKOFF_SECONDS` | no | `1.0` | Base exponential backoff for webhook retries |
| `WEBHOOK_REQUEST_TIMEOUT_SECONDS` | no | `10.0` | Per-attempt timeout for webhook HTTP calls |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Notification records | DynamoDB | `panakoes-notification` | read+write |
| SES | AWS SES | sender: `SES_FROM_ADDRESS` | send |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok"}` |
| POST | `/notify/email` | JWT Bearer | `{"to": email, "subject": str, "template": str, "vars": dict}` | 201 `{"notification_id": str, "status": "sent", "attempts": int}` |
| POST | `/notify/webhook` | JWT Bearer | `{"url": https_url, "payload": dict, "headers": dict\|null}` | 200 `{"notification_id": str, "status": "sent"\|"failed", "attempts": int}` |
| GET | `/notifications` | JWT Bearer | query: `limit`, `cursor` | `NotificationListResponse` |
| GET | `/notifications/{notification_id}` | JWT Bearer | -- | `NotificationRecord` or 404 |

### Notes

- Webhook URLs must be `https://` (validated at request time; `http://` returns 400).
- Email templates are Jinja2 templates loaded from `services/notification/` (see `templates_loader.py`).
- Audit events: `notification.email_sent`, `notification.webhook_sent`, `notification.webhook_failed`.
- `actor_type` is threaded through from the authenticated user's JWT claims.

---

## Summarization (`services/summarization/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-summarization`
**API GW route prefix:** `/v1/summarization/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `summarization` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `""` (empty; service fails with bad auth if unset) | HS256 shared secret |
| `JWT_ISSUER` | no | `panakoes-auth` | Expected JWT issuer (note: differs from other services which use `https://auth.panakoes.com`) |
| `JWT_AUDIENCE` | no | `panakoes` | Expected JWT audience (note: differs from other services which use `panakoes-api`) |
| `S3_TRANSCRIPTS_BUCKET` | no | `panakoes-dev-transcripts` | S3 bucket where transcript text files live |
| `S3_SUMMARIES_BUCKET` | no | `panakoes-dev-summaries` | S3 bucket where generated summary files are stored |
| `DDB_SUMMARIES_TABLE` | no | `panakoes-dev-summaries` | DynamoDB table for summary metadata |
| `ANTHROPIC_API_KEY` | yes (prod) | `""` | Anthropic Claude API key |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Transcripts | S3 | `panakoes-dev-transcripts` | read |
| Summaries (objects) | S3 | `panakoes-dev-summaries` | write |
| Summaries (metadata) | DynamoDB | `panakoes-dev-summaries` | read+write |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok", "service": "summarization"}` |
| POST | `/summarize` | JWT Bearer | `{"transcript_id": str, "tier": "standard"\|"deep"}` | `SummaryRecord` or 404 |
| GET | `/summary/{transcript_id}` | JWT Bearer | -- | `SummaryRecord` or 404 |
| GET | `/summaries` | JWT Bearer | query: `limit`, `cursor` | `SummariesPage` |

### Outbound calls to other Panakoes services

None. Calls Anthropic API directly.

### Notes

- **WARNING:** `JWT_ISSUER` default is `panakoes-auth` and `JWT_AUDIENCE` default is `panakoes`. These differ from every other service (which use `https://auth.panakoes.com` / `panakoes-api`). Verify and align in production env vars or the summarization service will reject every token from the auth service.
- Tier `standard` uses Claude Haiku 4.5; tier `deep` uses Claude Sonnet 4.6 (per CLAUDE.md locked decision).
- Audit events: `summarization.completed`, `summarization.failed`.

---

## GPU Spawner (`services/gpu-spawner/`)

**Runtime:** ECS Fargate
**Language:** Python
**Port:** 8000
**ECR repo:** `panakoes-dev-gpu-spawner`
**API GW route prefix:** `/v1/gpu-spawner/` (not confirmed -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICE_NAME` | no | `gpu-spawner` | Service identifier |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-replace-in-production` | HS256 shared secret; session-manager mints service-actor JWTs to call this service |
| `JWT_ISSUER` | no | `https://auth.panakoes.com` | Expected JWT issuer |
| `JWT_AUDIENCE` | no | `panakoes-api` | Expected JWT audience |
| `GPU_AMI_ID` | yes | `""` | AMI ID for the GPU instance (custom streaming AMI) |
| `GPU_SECURITY_GROUP_ID` | yes | `""` | Security group for GPU instances |
| `GPU_SUBNET_ID` | yes | `""` | Subnet for GPU instances |
| `GPU_INSTANCE_TYPE` | no | `g4dn.xlarge` | EC2 instance type |
| `GPU_IAM_INSTANCE_PROFILE` | no | `panakoes-dev-gpu-instance` | IAM instance profile ARN for GPU instances |
| `PROJECT_TAG` | no | `panakoes` | EC2 tag value for `Project` tag |
| `GPU_SPAWNER_TAG` | no | `panakoes-dev-gpu-spawner` | EC2 `Spawner` tag; must match IAM policy condition |
| `SESSION_MANAGER_WS_ENDPOINT` | no | `wss://session-manager.panakoes.com` | WebSocket endpoint GPU instance connects back to |
| `DEPLOYMENT_ENVIRONMENT` | no | `dev` | OTel resource attribute |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| GPU EC2 instances | EC2 | tagged `Spawner=panakoes-dev-gpu-spawner` | RunInstances, DescribeInstances, TerminateInstances |
| Audit log | DynamoDB | via `panakoes_audit` lib | write |

### Inbound HTTP API

| Method | Path | Auth | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | none | -- | `{"status": "ok"}` |
| POST | `/spawn` | service-actor JWT | `{"session_id": str, "user_id": str}` | 201 `{"instance_id": str}` |
| GET | `/spawn/{instance_id}` | JWT Bearer (user or service) | -- | `{"instance_id": str, "state": str, "session_id": str\|null, "user_id": str\|null}` or 404 |
| DELETE | `/spawn/{instance_id}` | service-actor JWT | -- | `{"instance_id": str, "previous_state": str, "current_state": str}` or 404/403 |

### Notes

- `POST /spawn` and `DELETE /spawn/{instance_id}` require `actor_type=service` JWT (403 for user JWTs).
- `GET /spawn/{instance_id}` is scoped for user actors: the instance's `SessionId` EC2 tag must match the caller's `session_id` JWT claim.
- Application-layer `Spawner` tag check guards against confused-deputy; IAM policy enforces the same constraint redundantly.
- Audit events: `gpu_spawner.spawned`, `gpu_spawner.terminated`.

---

## Cost Rollup Aggregator (`services/cost-rollup-aggregator/`)

**Runtime:** AWS Lambda (EventBridge Scheduler trigger, not ECS)
**Language:** Python
**Port:** N/A
**ECR repo:** (not found -- manual verification needed; Lambda may use a zip deployment)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TENANT_COST_ROLLUP_TABLE` | yes | `""` (raises RuntimeError if absent) | DynamoDB table for per-tenant daily cost rollup rows |
| `AWS_REGION` | no | `us-east-1` | AWS region (Lambda injects automatically) |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Tenant cost rollup | DynamoDB | value of `TENANT_COST_ROLLUP_TABLE` | read+write (upsert per tenant per day) |
| Cost Explorer | AWS CE API | N/A | read (GetCostAndUsage) |

### Lambda trigger

EventBridge Scheduler rule fires daily at 02:00 UTC. Event payload: `{}` uses yesterday-UTC as the target day; `{"day": "YYYY-MM-DD"}` overrides for manual replays.

### Notes

- Imports `panakoes_cost_api.tenant_rollup.TenantRollupStore` (cross-service library reuse). Any change to the rollup store's DynamoDB schema affects both `cost-api` and this Lambda.
- No retries configured; the EventBridge Scheduler fires once per day and the next nightly run naturally re-aggregates any failed day (upsert semantics).

---

## Event Router (`services/event-router/`)

**Runtime:** AWS Lambda (S3 ObjectCreated trigger, not ECS)
**Language:** Python
**Port:** N/A
**ECR repo:** (not found -- check `infra/dev/event-router/`)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DDB_INGESTION_TABLE` | yes | `""` (raises RuntimeError if absent) | DynamoDB ingestion table to mark `pending -> uploaded` |
| `EVENTBRIDGE_BUS_NAME` | yes | `""` (raises RuntimeError if absent) | EventBridge bus to publish `AudioUploaded` events |
| `AWS_REGION` | no | `us-east-1` | AWS region (Lambda injects automatically) |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Ingestion records | DynamoDB | value of `DDB_INGESTION_TABLE` | conditional update (`pending -> uploaded`) |
| EventBridge | EventBridge | value of `EVENTBRIDGE_BUS_NAME` | publish |
| CloudWatch metrics | CloudWatch | namespace `panakoes/dev/event-router` | write (`unknown_record` count) |

### Lambda trigger

S3 `ObjectCreated:*` on the audio-uploads bucket. Two event shapes supported: direct S3 notification (`event["Records"]`) and EventBridge wrapper (`event["source"] == "aws.s3"`).

### EventBridge events produced

| Event bus | Source | DetailType | Detail fields | When |
|---|---|---|---|---|
| `EVENTBRIDGE_BUS_NAME` | `panakoes.ingest` | `AudioUploaded` | `{user_id, ingestion_id, filename}` | on successful `pending -> uploaded` transition |

### Notes

- Idempotent: re-delivering the same S3 event is a no-op (conditional update with `#s = :pending` condition).
- Object key format expected: `audio/{user_id}/{ingestion_id}/{filename}` (parsed by `key_parser.py`).
- Handler function: `panakoes_event_router.handler.lambda_handler`.

---

## Transcribe Worker (`services/transcribe-worker/`)

**Runtime:** AWS Lambda (SQS event-source mapping, not ECS)
**Language:** Python
**Port:** N/A
**ECR repo:** (not found -- check `infra/dev/transcribe-worker/`)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DDB_INGESTION_TABLE` | yes | `""` (raises RuntimeError if absent) | DynamoDB ingestion table for status updates |
| `AUDIO_UPLOADS_BUCKET` | yes | `""` (raises RuntimeError if absent) | S3 bucket to download audio for transcription |
| `AWS_REGION` | no | `us-east-1` | AWS region (Lambda injects automatically) |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Ingestion records | DynamoDB | value of `DDB_INGESTION_TABLE` | read+write (status updates) |
| Audio files | S3 | value of `AUDIO_UPLOADS_BUCKET` | read |

### SQS messages consumed

| Queue | Source | Message shape | When |
|---|---|---|---|
| (configured via EventBridge Scheduler) | EventBridge rule on default bus matching S3 ObjectCreated | EventBridge envelope wrapping S3 event | on audio upload |

### Notes

- Each SQS record body is a JSON-stringified EventBridge envelope around the S3 event (NOT a raw S3 record).
- Failure handling: transient errors (rate limit) re-raise; terminal errors log and return success so SQS deletes the message. DLQ catches surprise crashes after 3 receive attempts.
- Imports and calls `panakoes_ingestion_api.transcription.transcribe_ingestion` -- the same function used by the on-demand route. Any refactor of that module affects both callers.
- Handler function: `panakoes_transcribe_worker.handler.lambda_handler`.

---

## Streaming Router (`services/streaming-router/`)

**Runtime:** AWS Lambda (API Gateway v2 WebSocket routes, not ECS)
**Language:** Python
**Port:** N/A
**ECR repo:** (not found -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `STREAMING_SESSIONS_TABLE` | yes | `""` (raises RuntimeError if absent) | DynamoDB streaming sessions table |
| `AUDIO_FRAME_QUEUE_URL` | yes | `""` (raises RuntimeError if absent) | SQS queue URL for audio frame forwarding |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `STREAMING_EVENT_BUS` | no | `default` | EventBridge bus for streaming lifecycle events |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Streaming sessions | DynamoDB | value of `STREAMING_SESSIONS_TABLE` | read+write |
| Audio frame queue | SQS | value of `AUDIO_FRAME_QUEUE_URL` | send |
| Streaming event bus | EventBridge | value of `STREAMING_EVENT_BUS` | publish |

### WebSocket route handlers

| Route key | Description |
|---|---|
| `$connect` | Persist session row + trigger gpu-spawner |
| `$disconnect` | Mark session as disconnected |
| `audio-frame` | Forward audio chunk to SQS queue |
| `transcript-request` | (not found -- manual verification needed) |
| `$default` | No-op (forward-compat) |

### Notes

- API Gateway v2 WebSocket authorizer (`ws-authorizer` Lambda) validates the JWT on `$connect` before this handler runs.
- The authorizer context (populated by `ws-authorizer`) provides `user_id`, `tenant_id`, `role` under `requestContext.authorizer.lambda`.
- Handler function: `panakoes_streaming_router.router.lambda_handler` (not confirmed -- check for a `lambda_handler` wrapper around `Router.from_env().handle(event)`).

---

## WS Authorizer (`services/ws-authorizer/`)

**Runtime:** AWS Lambda (API Gateway v2 custom authorizer)
**Language:** Python
**Port:** N/A
**ECR repo:** (not found -- manual verification needed)

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET` | yes | -- | HS256 shared secret (read via `panakoes_auth_client.from_env()`) |
| `JWT_ISSUER` | yes | -- | Expected JWT issuer |
| `JWT_AUDIENCE` | yes | -- | Expected JWT audience |
| `JWT_PUBLIC_JWKS_URL` | no | -- | If set, switches to RS256+JWKS mode |

### Inbound

API Gateway v2 WebSocket authorizer event on `$connect`. Identity sources (in priority order):

1. `Authorization: Bearer <jwt>` header.
2. `?token=<jwt>` query string parameter (browser microphone path).

### Outbound response

```json
{"isAuthorized": true, "context": {"user_id": "...", "role": "...", "tenant_id": "..."}}
// or
{"isAuthorized": false}
```

### Notes

- If the `Authorization` header is present but malformed, falls through to `{"isAuthorized": false}` without checking the `?token` query param (strict precedence, no silent downgrade).
- `tenant_id` is extracted by re-decoding the JWT unverified after signature validation (safe read of extra claims that the strict `JwtClaims` model drops).
- Module-level `_VALIDATOR` cache means the JWT config is read once per cold start.
- Handler function: `panakoes_ws_authorizer.authorizer.lambda_handler`.

---

## Transcriber Batch (`services/transcriber-batch/`)

**Runtime:** AWS Batch (EC2 g4dn.xlarge Spot, not ECS or Lambda)
**Language:** Python
**Port:** N/A
**ECR repo:** `panakoes-dev-transcriber-batch`

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `S3_INPUT_URI` | yes | -- | `s3://<bucket>/<key>` of the source audio (per-job Batch parameter) |
| `S3_OUTPUT_PREFIX` | yes | -- | `s3://<bucket>/<prefix>` for transcript artifacts (per-job Batch parameter) |
| `JOB_ID` | yes | -- | AWS Batch job ID, for log correlation (per-job Batch parameter) |
| `SESSION_ID` | yes | -- | Owning streaming-sessions row ID (per-job Batch parameter) |
| `MODEL_PATH` | no | `/opt/whisper/models/large-v3.pt` | On-disk Whisper-large-v3 fp16 weights (baked into AMI) |
| `SESSIONS_TABLE` | no | `panakoes-dev-streaming-sessions` | DynamoDB table name for session status updates |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `DEVICE` | no | `cuda` | Compute device (`cuda` on GPU AMI; `cpu` for local debug) |

### AWS resources owned (reads/writes)

| Resource | Type | Name/ARN pattern | Access |
|---|---|---|---|
| Audio source | S3 | value from `S3_INPUT_URI` | read |
| Transcript output | S3 | value from `S3_OUTPUT_PREFIX` | write |
| Streaming sessions | DynamoDB | value of `SESSIONS_TABLE` | write (status: `active -> completed` or `errored`) |

### Notes

- Per-job Batch parameters (`S3_INPUT_URI`, `S3_OUTPUT_PREFIX`, `JOB_ID`, `SESSION_ID`) are injected via the Batch job definition's `parameters` block.
- Container-static config (`MODEL_PATH`, `SESSIONS_TABLE`, etc.) comes from the job definition's `environment` block.
- Lifecycle: mark `active`, download audio, load Whisper, transcribe, upload JSON, mark `completed`. Failures mark `errored` and exit 1.
- Whisper model: `whisper-large-v3 fp16`; faster-whisper + Silero VAD for streaming (different from this batch path).
- Entrypoint: `panakoes_transcriber_batch.main` (called as a Python module).

---

## Transcriber Groq (`services/transcriber-groq/`)

**Runtime:** Library (not deployed; imported by other services)
**Language:** Python
**Package name:** `panakoes-transcriber-groq`
**Python package:** `panakoes_transcriber_groq`

### What it exports

- `GroqTranscriberBackend`: a concrete `Transcriber` (from `panakoes-transcriber-lib`) backed by Groq's hosted Whisper-large-v3.
- Constructor takes `api_key` (required), `model`, `base_url`, `timeout_seconds`, `transport` (for test injection).
- API key pulled from `GROQ_API_KEY` env var by callers (or `panakoes-dev/groq-api-key` in AWS Secrets Manager for deployed envs). The backend itself is env-agnostic.

### Which services import it

- `services/ingestion-api` (via `transcription.py`; selects the backend based on env var dispatch in `get_transcriber()`)
- `services/transcribe-worker` (via same `transcription.py`)

### Notes

- Default base URL: `https://api.groq.com/openai/v1`
- Default model: `whisper-large-v3`
- Default timeout: 60s
- Error hierarchy: `TranscriberAuthError` (401), `TranscriberRateLimitError` (429, parsed `Retry-After`), `TranscriberRequestError` (4xx), `TranscriberUpstreamError` (5xx), `TranscriberTimeoutError` (network).

---

## Transcriber Lib (`services/transcriber-lib/`)

**Runtime:** Library (not deployed; imported by all transcription-touching services)
**Language:** Python
**Package name:** `panakoes-transcriber`
**Python package:** `panakoes_transcriber`

### What it exports

- `Transcriber`: a `runtime_checkable` `Protocol` defining `async transcribe(*, audio_bytes, filename, language_hint) -> TranscriptionResult`.
- `TranscriptionResult`: result type with `text`, `segments`, `language`, `duration_seconds`.
- `TranscriptionSegment`: `text`, `start`, `end`, `words`.
- `Word`: `text`, `start`, `end`.
- Error hierarchy: `TranscriberAuthError`, `TranscriberRateLimitError`, `TranscriberRequestError`, `TranscriberUpstreamError`, `TranscriberTimeoutError`.

### Which services import it

- `panakoes_transcriber_groq` (implements the Protocol)
- `services/ingestion-api` (uses `Transcriber` Protocol type hints)
- `services/transcribe-worker` (uses error hierarchy for failure routing)

---

## Audit Lib (`services/audit-lib/`)

**Runtime:** Library (not deployed; imported by every service that emits audit events)
**Language:** Python
**Package name:** `panakoes-audit`
**Python package:** `panakoes_audit`

### What it exports

- `record_event(actor_id, actor_type, action, resource_type, resource_id, source_service, details)`: the public coroutine all services call.
- `AuditEvent`: Pydantic model (validated before persistence).
- `AuditStore`: Protocol for the write surface.
- `DynamoDBAuditStore`: production store (writes to DynamoDB).
- `MemoryAuditStore`: test store.
- `StdoutAuditStore`: local dev store.
- `AuditSettings`: configuration model.
- `set_store`, `reset_store`: control the active store (used in tests).

### Environment variables (AUDIT_ prefix)

| Variable | Required | Default | Description |
|---|---|---|---|
| `AUDIT_BACKEND` | no | `stdout` | `dynamodb`, `stdout`, or `memory` |
| `AUDIT_TABLE_NAME` | no | `panakoes-audit-log` | DynamoDB table for audit events |
| `AUDIT_AWS_REGION` | no | `us-east-1` | AWS region for DynamoDB client |

### Which services import it

Every Python service: `ingestion-api`, `admin-api`, `billing`, `session-manager`, `query-api`, `notification`, `summarization`, `gpu-spawner`.

---

## Auth Client (`services/auth-client/`)

**Runtime:** Library (not deployed; imported by every Python service that validates JWTs)
**Language:** Python
**Package name:** `panakoes-auth-client`
**Python package:** `panakoes_auth_client`

### What it exports

- `JwtValidator`: validates HS256 (shared secret) or RS256 (JWKS) tokens.
- `from_env()`: construct a validator from env vars (reads `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE` for HS256; reads `JWT_PUBLIC_JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE` for RS256/JWKS mode).
- `JwtClaims`: Pydantic model with fields `sub`, `email`, `role`, `jti`, `iss`, `aud`, `exp`.
- `JwtInvalidError`: raised for any token-level failure (expired, wrong issuer, bad signature, revoked session).
- `JwtConfigError`: raised when required env vars are missing.
- `fastapi_dependency`: FastAPI `Depends`-compatible factory.
- `JwksCache`: async JWKS key cache for RS256 mode.

### Environment variables (read by `from_env()`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET` | yes (HS256 mode) | -- | HS256 shared secret |
| `JWT_ISSUER` | yes | -- | Expected `iss` claim |
| `JWT_AUDIENCE` | yes | -- | Expected `aud` claim |
| `JWT_PUBLIC_JWKS_URL` | no | -- | If set, activates RS256+JWKS mode (reads from this URL) |

### Which services import it

Every Python service that validates JWTs: `ingestion-api`, `admin-api`, `billing`, `cost-api`, `session-manager`, `query-api`, `notification`, `summarization`, `gpu-spawner`, `ws-authorizer`.

---

## Middleware Lib (`services/middleware-lib/`)

**Runtime:** Library (not deployed; imported by FastAPI services that need cross-cutting HTTP middleware)
**Language:** Python
**Package name:** `panakoes-middleware`
**Python package:** `panakoes_middleware`

### What it exports

- `RateLimitMiddleware`: sliding-window rate limiter with `InMemoryStore` (dev) and `RedisStore` (prod).
- `make_cors_middleware`, `from_env`: CORS middleware with safe defaults and env-driven allowed origins.
- `MaxRequestSizeMiddleware`: rejects requests above a configurable body size.
- `RequiredHeadersMiddleware`: gates on required request headers.
- `CorrelationIdMiddleware`, `get_request_id`, `RequestIdLogFilter`: `X-Request-Id` propagation via contextvar.
- `audit_event`: decorator that emits a structured audit event after a route handler runs.
- `require_plan`, `Plan`: plan-gating decorator for feature-flagging by subscription tier.

### Which services import it

(not found -- manual verification needed; lib is available but actual import usage per service was not surveyed)

---

## Models Lib (`services/models-lib/`)

**Runtime:** Library (not deployed; shared Pydantic models for cross-service contracts)
**Language:** Python
**Package name:** `panakoes-models`
**Python package:** `panakoes_models`

### What it exports

- `IngestionRecord`, `IngestionId`, `IngestionStatus`, `MAX_INGESTION_SIZE_BYTES`
- `Transcript`, `TranscriptId`, `TranscriptSegment`
- `Summary`, `SummaryId`, `SummaryTier`
- `StreamingSession`, `SessionId`, `SessionStatus`
- `NotificationRecord`, `NotificationId`, `NotificationKind`, `NotificationStatus`
- `Subscription`, `BillingTier`
- `User`, `UserId`, `UserRole`, `UserTier`
- `ApiError`

All models use `frozen=True` and `extra="forbid"`.

### Notes

This library is the canonical source of truth for field shapes and validation rules. Unknown fields at construction time raise `ValidationError`. This is intentional -- it is a contract, and silent acceptance of unknown fields would make breaking changes invisible.

---

## OTel Lib (Python) (`services/otel-lib/`)

**Runtime:** Library (not deployed; imported by every Python service for OpenTelemetry instrumentation)
**Language:** Python
**Package name:** `panakoes-otel`
**Python package:** `panakoes_otel`

### What it exports

- `configure(service_name, environment)`: configures OTel SDK with OTLP/gRPC exporter. Set `OTEL_SDK_DISABLED=true` to wire NoOp providers (for tests).
- `instrument_fastapi(app)`: auto-instrumentation for FastAPI.
- `instrument_boto3()`: auto-instrumentation for boto3 (covers DynamoDB, S3, SQS, EventBridge).
- `instrument_httpx()`: auto-instrumentation for httpx.
- `get_tracer(name)`, `get_meter(name)`: named tracer/meter factories.
- `shutdown()`: flush exporters on shutdown.

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | `http://localhost:4317` | OTLP/gRPC collector endpoint |
| `OTEL_SDK_DISABLED` | no | `false` | Set to `true` to disable all exporters (for tests) |
| `SERVICE_VERSION` | no | `0.0.0` | Populates `service.version` OTel resource attribute |

### Which services import it

Every Python service: `ingestion-api`, `admin-api`, `billing`, `cost-api`, `session-manager`, `query-api`, `notification`, `summarization`, `gpu-spawner`, `event-router`, `transcribe-worker`.

---

## OTel Lib (TypeScript) (`services/otel-lib-ts/`)

**Runtime:** Library (not deployed; imported by TypeScript services)
**Language:** TypeScript
**Package name:** `@panakoes/otel`

### What it exports

- `configure(options: {serviceName, environment})`: configure OTel SDK, returns `NodeSDK | undefined`.
- `shutdown(sdk)`: async flush.
- `getMeter(name)`, `getTracer(name)`: named meter/tracer factories.
- `instrumentHono(app)`: Hono-specific HTTP instrumentation middleware.
- `instrumentations`: array of default instrumentations.

### Which services import it

- `services/auth` (TypeScript auth service)
