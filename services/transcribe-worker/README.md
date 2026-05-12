# transcribe-worker

SQS-driven Lambda that auto-fires transcription on every audio upload.

## Pipeline

```
S3 (audio-uploads bucket)
  -> ObjectCreated (EventBridge notifications enabled on the bucket)
  -> EventBridge default bus
  -> Rule (filter: bucket + key prefix `audio/`)
  -> SQS (panakoes-dev-transcribe-trigger)
  -> Lambda (this service)
  -> panakoes_ingestion_api.transcription.transcribe_ingestion()
  -> DynamoDB ingestion record updated with transcript
```

## Why this exists

Before this Lambda, transcription was on-demand only via
`POST /api/v1/transcribe/{ingestion_id}` on the Ingestion API.
Clients had to upload AND then make a second call. This worker
removes the second call: the user uploads, S3 fires, transcription
happens automatically.

The on-demand route still works (manual retries, front-end-driven
re-runs); both call sites share the same `transcribe_ingestion`
orchestration so behavior stays single-sourced (ADR-009).

## Failure handling

Per the SQS `ReportBatchItemFailures` contract:

- **Transient** (`TranscriberRateLimitError`): re-raise via
  `batchItemFailures` so SQS retries after the visibility timeout.
- **Terminal** (auth, upstream, malformed key, missing record):
  log + persist `transcript_status=failed` via the existing
  `transcribe_ingestion` flow, return success so SQS deletes
  the message. The DLQ collects only surprise crashes.
- **Already done** (`transcript_status=succeeded` or `pending`):
  short-circuit; idempotent against re-delivery.

## Operator follow-up before this works in deployed env

1. Apply `infra/dev/transcribe-worker/` (`scripts/tf.sh apply transcribe-worker`).
2. Build + push the container image to ECR via the canonical GHA bake
   (`.github/workflows/image-bake-on-change.yml` on push to `main`, or
   trigger `image-bake-manual.yml` for `transcribe-worker` from the
   Actions UI). Local `docker build -f services/transcribe-worker/Dockerfile .`
   + `docker push <repo>:<tag>` is a fallback for offline dev only;
   see operator guide Section E for the rare cases that need it.
3. Populate `panakoes-dev/groq-api-key` in Secrets Manager
   (the Lambda reads it via the env-var-injected secret reference;
   Section D in the operator guide).

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `DDB_INGESTION_TABLE` | yes | DynamoDB ingestion table name |
| `AUDIO_UPLOADS_BUCKET` | yes | S3 audio-uploads bucket name |
| `AWS_REGION` | auto | Set by Lambda runtime |
| `TRANSCRIBER_BACKEND` | no | `groq` (default) or `openai` |
| `GROQ_API_KEY` | yes (groq) | Backed by Secrets Manager via env-var ref |
| `DEPLOYMENT_ENVIRONMENT` | no | Used by panakoes-otel; defaults to `dev` |
