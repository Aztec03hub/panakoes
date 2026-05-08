# services/query-api

Query API microservice for Panakoes. The unified read API over a
user's ingestion records, transcript summaries, and live streaming
sessions. This is the primary backend for the user-facing dashboard.

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET | `/health` | no | Liveness probe |
| GET | `/ingestions` | yes | List the caller's ingestion records (paginated, default 25, max 100) |
| GET | `/ingestions/{id}` | yes | Fetch one of the caller's ingestion records |
| GET | `/summaries` | yes | List the caller's summaries (paginated, default 25, max 100) |
| GET | `/summaries/{transcript_id}` | yes | Fetch one of the caller's summaries |
| GET | `/sessions` | yes | List the caller's streaming sessions (paginated, default 25, max 100) |
| GET | `/sessions/{id}` | yes | Fetch one of the caller's streaming sessions |

Cross-user access (caller asks for a record they do not own) returns
HTTP 404, never 403, so the existence of other users' records is not
leaked. Same security pattern as the Ingestion API.

## Configuration

Read from environment variables (see `src/panakoes_query_api/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `DDB_INGESTION_TABLE` | `panakoes-dev-ingestion` | Provisioned by Terraform |
| `DDB_SUMMARIES_TABLE` | `panakoes-dev-summaries` | Provisioned by the Summarization service |
| `DDB_SESSIONS_TABLE` | `panakoes-dev-streaming-sessions` | Provisioned by Terraform |
| `AWS_REGION` | `us-east-1` | |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint (ADOT in prod) |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` in tests + offline dev to wire NoOp providers |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |
| `DEPLOYMENT_ENVIRONMENT` | `dev` | Stamped onto the `deployment.environment` resource attribute |

## Authentication

All endpoints except `/health` require `Authorization: Bearer <jwt>`.
The token must be HS256-signed with `JWT_SECRET` and carry the
documented Auth-service payload (`sub`, `email`, `jti`, `iss`, `aud`,
`iat`, `exp`).

## DynamoDB schemas

The tables are provisioned out-of-band by Terraform.

**Ingestions table** (`DDB_INGESTION_TABLE`):
- `pk = "USER#" + user_id`
- `sk = "INGESTION#" + ingestion_id`

**Summaries table** (`DDB_SUMMARIES_TABLE`):
- `pk = "USER#" + user_id`
- `sk = "SUMMARY#" + transcript_id`

**Sessions table** (`DDB_SESSIONS_TABLE`):
- `pk = session_id` (bare partition key)
- GSI `UserSessionsIndex` (hash=user_id, range=created_at) backs the
  list-for-user access pattern.

## Pagination

All list endpoints take `limit` (default 25, max 100) and an optional
`cursor` query parameter. The cursor is the bare record id of the
last item on the previous page; the server reconstructs the
DynamoDB sort key (or GSI key, for sessions) internally so callers
do not need to URL-encode `#` separators.

## Audit events

The service emits two audit-log actions via `panakoes-audit`:
- `query.records_listed` on every list call
- `query.record_fetched` on every successful single-record fetch

Source service identifier: `query-api`.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_query_api.main:app --reload
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
docker build -t panakoes-query-api .
```
