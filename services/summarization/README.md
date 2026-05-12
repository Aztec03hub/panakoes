# services/summarization

The Panakoes summarization microservice. Reads transcripts from S3,
calls the Anthropic Claude API for summarization, persists the result
to S3 and DynamoDB, and exposes owner-scoped retrieval endpoints.

This is the v0.1 MVP. Per the locked decision in CLAUDE.md, the default
tier uses `claude-haiku-4-5` and the paid-tier deep summary uses
`claude-sonnet-4-6`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | Liveness; returns `{"status":"ok","service":"summarization"}` |
| POST | `/summarize` | Summarize a transcript and persist the result |
| GET  | `/summary/{transcript_id}` | Owner-only fetch of summary metadata |
| GET  | `/summaries` | Paginated list of summaries owned by the JWT subject |

## Authentication

All non-health endpoints require an `Authorization: Bearer <jwt>` header.
The token must be HS256-signed with the secret in `JWT_SECRET`, issued
by `JWT_ISSUER`, and audienced to `JWT_AUDIENCE`. The contract mirrors
the auth service (ADR-005). The `sub` claim is treated as the owner
identity for resource scoping; cross-user access returns 404 to avoid
leaking the existence of another user's transcripts.

`auth.py` is held to 100% coverage per ADR-018.

## Storage layout

| Resource | Location |
|---|---|
| Transcript text | `s3://${S3_TRANSCRIPTS_BUCKET}/transcripts/{user_id}/{transcript_id}.txt` |
| Summary markdown | `s3://${S3_SUMMARIES_BUCKET}/summaries/{user_id}/{transcript_id}.md` |
| Summary metadata | DynamoDB `${DDB_SUMMARIES_TABLE}` with `pk = USER#{user_id}`, `sk = SUMMARY#{transcript_id}` |

## Audit events

The service writes two action types via `panakoes-audit`:

- `summarization.completed` on success
- `summarization.failed` when the transcript is missing or the Anthropic call raises

`source_service` is `summarization` for both.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `JWT_SECRET` | yes | HS256 signing secret (must match the auth service) |
| `JWT_ISSUER` | no | Default `panakoes-auth` |
| `JWT_AUDIENCE` | no | Default `panakoes` |
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `S3_TRANSCRIPTS_BUCKET` | no | Default `panakoes-dev-transcripts` |
| `S3_SUMMARIES_BUCKET` | no | Default `panakoes-dev-summaries` |
| `DDB_SUMMARIES_TABLE` | no | Default `panakoes-dev-summaries` |
| `AWS_REGION` | no | Default `us-east-1` |
| `LOG_LEVEL` | no | Default `INFO` |

In production, `JWT_SECRET` and `ANTHROPIC_API_KEY` come from AWS
Secrets Manager via the deployment pipeline. They are never committed.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_summarization.main:app --reload
```

## Testing

```bash
uv run ruff check
uv run mypy src
uv run pytest
```

Integration tests use `moto` to mock S3 and DynamoDB. The Anthropic
SDK is replaced with a fake `Summarizer` via FastAPI's
`dependency_overrides`, so no live API calls happen in CI. Coverage
is enforced at 80% overall by `--cov-fail-under=80` in
`pyproject.toml`; `auth.py` is exercised to 100% in its dedicated
unit-test module.

## Building the Docker image

Canonical bake path is GitHub Actions (`.github/workflows/image-bake-on-change.yml` on push to `main`, or the `image-bake-manual.yml` one-button workflow). The local command below is a fallback for offline dev.

```bash
docker build -t panakoes-summarization .
```

The Dockerfile is multi-stage: a builder stage installs `uv`-managed
dependencies into `/opt/venv`, and the runtime stage copies that
virtualenv plus `src/` into a minimal `python:3.12-slim` image,
running as a non-root `app` user on port 8000.

## Architecture notes

- The system prompt is sent with `cache_control: {"type": "ephemeral"}`,
  which lets the Anthropic prompt cache amortize the system tokens
  across the warm window. Verify hits via
  `response.usage.cache_read_input_tokens` (`shared/prompt-caching.md`
  documents the audit checklist).
- Owner-scoping is enforced by deriving every S3 key and DynamoDB
  partition key from the JWT subject. A handler cannot accidentally
  read another user's resource because the user_id never comes from
  the request body.
- The DynamoDB write happens after the S3 write succeeds. If the
  metadata write fails, the markdown is left in S3; a follow-up
  reconciliation job is out of scope for v0.1 but the schema supports
  it (the S3 key is derivable from the user_id and transcript_id).
