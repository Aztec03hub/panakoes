# services/ingestion-api

Ingestion API microservice for Panakoes. Authenticated clients call it
to obtain pre-signed S3 URLs and start an upload; the resulting upload
intent is recorded in DynamoDB. The S3 event handler that finalizes
the ingestion when the upload completes lives in a separate Lambda
(next slice).

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET  | `/health`                  | no  | Liveness probe |
| POST | `/ingestion/audio`         | yes | Create upload intent, return pre-signed PUT URL |
| GET  | `/ingestion/{ingestion_id}`| yes | Fetch one of the caller's records |
| GET  | `/ingestion`               | yes | List the caller's records (paginated, default 25, max 100) |

## Configuration

Read from environment variables (see `src/panakoes_ingestion_api/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTH_JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret |
| `AUTH_JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `AUTH_JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `INGESTION_TABLE_NAME` | `panakoes-ingestion` | Provisioned by Terraform |
| `INGESTION_BUCKET` | `panakoes-audio-uploads` | Provisioned by Terraform |
| `PRESIGNED_URL_TTL_SECONDS` | `900` | 15 minutes |
| `AWS_REGION` | `us-east-1` |  |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint (ADOT in prod) |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` in tests + offline dev to wire NoOp providers |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |
| `DEPLOYMENT_ENVIRONMENT` | `dev` | Stamped onto the `deployment.environment` resource attribute |

## Authentication

All endpoints except `/health` require `Authorization: Bearer <jwt>`.
The token must be HS256-signed with `AUTH_JWT_SECRET` and carry the
documented Auth-service payload (`sub`, `email`, `jti`, `iss`, `aud`,
`iat`, `exp`).

## DynamoDB schema

The table is provisioned out-of-band by Terraform. This service writes
records with this shape:

- `pk = "USER#" + user_id`
- `sk = "INGESTION#" + ingestion_id`
- attributes: `ingestion_id`, `user_id`, `filename`, `content_type`,
  `size_bytes`, `s3_key`, `status` (`pending` | `uploaded` | `failed`),
  `created_at`, `updated_at`

## S3 layout

Object key: `audio/{user_id}/{ingestion_id}/{sanitized_filename}`.
Filenames are reduced to ASCII alphanum + hyphen + dot + underscore.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_ingestion_api.main:app --reload
```

## Running tests

```bash
uv run pytest
```

## Linting and type checking

```bash
uv run ruff check
uv run mypy src
```

## Building the Docker image

```bash
docker build -t panakoes-ingestion-api .
```
