# services/ingestion-api

Ingestion API microservice for Panakoes. Authenticated clients call it to obtain pre-signed S3 URLs and start an upload; the resulting upload intent is recorded in DynamoDB. The S3 event handler that finalizes the ingestion when the upload completes lives in the sibling `event-router` Lambda.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | no | Liveness probe |
| POST | `/ingestion/audio` | yes | Create upload intent, return pre-signed PUT URL |
| GET | `/ingestion/{ingestion_id}` | yes | Fetch one of the caller's records |
| GET | `/ingestion` | yes | List the caller's records (paginated, default 25, max 100) |
| POST | `/api/v1/transcribe/{ingestion_id}` | yes | Transcribe an uploaded ingestion via the configured backend (on-demand) |

All endpoints except `/health` require `Authorization: Bearer <jwt>`. The token must be HS256-signed with the shared secret (the auth service signs from `AUTH_JWT_SECRET`; this validator reads `JWT_SECRET`, see `CONTRIBUTING.md`) and carry the documented Auth-service payload (`sub`, `email`, `jti`, `iss`, `aud`, `iat`, `exp`).

## Environment variables

Read from environment variables (see `src/panakoes_ingestion_api/config.py`):

| Variable | Required / Default | Description |
|---|---|---|
| `JWT_SECRET` | required (dev placeholder in `.env.example`) | Must match the Auth service's HS256 secret (auth signs from `AUTH_JWT_SECRET`; validators read `JWT_SECRET`, see `CONTRIBUTING.md`) |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `INGESTION_TABLE_NAME` | `panakoes-ingestion` | Provisioned by Terraform |
| `INGESTION_BUCKET` | `panakoes-audio-uploads` | Provisioned by Terraform |
| `PRESIGNED_URL_TTL_SECONDS` | `900` | 15 minutes |
| `AWS_REGION` | `us-east-1` | AWS region |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint (ADOT in prod) |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` in tests + offline dev to wire NoOp providers |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |
| `DEPLOYMENT_ENVIRONMENT` | `dev` | Stamped onto the `deployment.environment` resource attribute |
| `TRANSCRIBER_BACKEND` | `groq` | Selects the transcription backend; supported: `groq`, `openai` (latter requires `panakoes-transcriber-openai` installed) |
| `GROQ_API_KEY` | required when `TRANSCRIBER_BACKEND=groq` | Source via Secrets Manager (`panakoes-dev/groq-api-key`, follow-up PR) in deployed envs; via env var locally |
| `OPENAI_API_KEY` | required when `TRANSCRIBER_BACKEND=openai` | Same sourcing pattern as `GROQ_API_KEY` |

## Local development

```bash
uv sync --group dev
uv run uvicorn panakoes_ingestion_api.main:app --reload
uv run pytest
uv run ruff check
uv run mypy src
```

## Deployment

```bash
docker build -t panakoes-ingestion-api .
```

The image is pushed to ECR and deployed via Terraform-managed ECS / Fargate (TODO: wire the Terraform module once infra slice lands). DynamoDB table and S3 bucket are provisioned out-of-band by Terraform.

## Architecture notes

- **DynamoDB schema** for `INGESTION_TABLE_NAME`:
  - `pk = "USER#" + user_id`
  - `sk = "INGESTION#" + ingestion_id`
  - attributes: `ingestion_id`, `user_id`, `filename`, `content_type`, `size_bytes`, `s3_key`, `status` (`pending` | `uploaded` | `failed`), `created_at`, `updated_at`.
  - transcription attributes (added by the transcription flow, optional until set): `transcript_status` (`pending` | `succeeded` | `failed`), `transcript` (Map containing `text`, `segments[]`, `language`, `duration_seconds`), `transcript_error_message` (string, set when `transcript_status = failed`).
- **S3 layout:** object key `audio/{user_id}/{ingestion_id}/{sanitized_filename}`. Filenames are reduced to ASCII alphanum + hyphen + dot + underscore at validation time.
- **Coverage gate:** 80% per ADR-018 (application-services tier).

## Transcription

The service ships with the pluggable `Transcriber` abstraction wired in (see ADR-009 and `services/transcriber-lib/`). The default backend is Groq Whisper-large-v3 via Groq's hosted OpenAI-compatible API; the OpenAI Whisper backend is selectable via env var once `panakoes-transcriber-openai` is installed; a self-hosted Whisper-on-GPU backend is planned.

End-to-end flow today:
1. Client uploads audio via `POST /ingestion/audio` -> pre-signed S3 PUT URL (unchanged).
2. **Auto-trigger (primary path):** the S3 ObjectCreated event flows S3 -> EventBridge default bus -> SQS (`panakoes-dev-transcribe-trigger`) -> the `services/transcribe-worker` Lambda, which calls the same `transcribe_ingestion` orchestration this route uses. No client action required after the upload completes.
3. **On-demand (manual path, still supported):** the client issues `POST /api/v1/transcribe/{ingestion_id}` (e.g. for retries or front-end-driven re-runs). The route validates ownership, marks `transcript_status = pending`, and schedules the transcription on `BackgroundTasks`. The HTTP response returns immediately with the (now-pending) record.
4. Either path: the underlying orchestration fetches the audio bytes from `INGESTION_BUCKET` using the record's `s3_key`, calls the configured backend, and writes the result back via `UpdateItem`. On any backend or fetch failure, `transcript_status` becomes `failed` with a short `transcript_error_message` so the front-end can render meaningfully.
5. The existing `GET /ingestion/{id}` returns the transcript fields when present; absent fields stay absent for unflushed records (no breaking change).

Idempotency:
- `succeeded` -> 200 with the existing transcript (no re-run).
- `pending` -> 200 with the in-flight record (no double-schedule).
- `failed` or no transcript yet -> 202-style response + (re)schedule.

**Auto-trigger pipeline:** see `services/transcribe-worker` and `infra/dev/transcribe-worker` for the SQS-driven consumer that fires `transcribe_ingestion` on every S3 ObjectCreated event. The on-demand route shares the same orchestration so behavior stays single-sourced.

**Backend selection:** set `TRANSCRIBER_BACKEND=groq` (default) or `=openai`. Each backend reads its own API key env var (`GROQ_API_KEY`, `OPENAI_API_KEY`); the dispatch fails fast with a clear `RuntimeError` if the key is absent. In deployed environments, the API key should land in AWS Secrets Manager at `panakoes-dev/groq-api-key` (operator follow-up: add the secret resource to `infra/dev/secrets/main.tf` in a separate PR).

### Smoke test (local, real Groq key)

```bash
export GROQ_API_KEY="gsk_..."
export TRANSCRIBER_BACKEND=groq
export JWT_SECRET="<your local secret>"
uv run uvicorn panakoes_ingestion_api.main:app --reload &

# Mint a JWT (use scripts/mint-test-jwt.py if available, or any HS256 helper).
TOKEN="<bearer>"

# 1. Create the ingestion intent.
INGESTION_ID=$(curl -s -X POST http://localhost:8000/ingestion/audio \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"sample.m4a","content_type":"audio/mp4","size_bytes":'"$(stat -c%s sample.m4a)"'}' \
  | jq -r .ingestion_id)

# 2. Upload to the pre-signed URL (re-fetch + curl PUT). Already exercised by the integration tests.

# 3. Trigger the transcription.
curl -s -X POST "http://localhost:8000/api/v1/transcribe/$INGESTION_ID" \
  -H "Authorization: Bearer $TOKEN" | jq

# 4. Poll until succeeded.
curl -s "http://localhost:8000/ingestion/$INGESTION_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.transcript_status, .transcript.text'
```
