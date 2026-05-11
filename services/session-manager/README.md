# services/session-manager

Session Manager microservice for Panakoes. Authenticated clients call it
to create and manage live streaming-transcription sessions. State is
persisted in DynamoDB; rows expire 8 hours after creation via the table's
TTL attribute. The GPU instance that backs each session is provisioned
out-of-band (see the streaming-spawner Lambda, next slice); this service
only owns the session record's lifecycle.

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET    | `/health`                | no  | Liveness probe |
| POST   | `/sessions`              | yes | Create a new session (status=starting) |
| GET    | `/sessions/{session_id}` | yes | Fetch one of the caller's sessions |
| GET    | `/sessions`              | yes | List the caller's sessions (paginated, default 25, max 100) |
| PATCH  | `/sessions/{session_id}` | yes | Update status and/or attach a GPU instance id |
| DELETE | `/sessions/{session_id}` | yes | Soft-delete (status=errored, expires_at=now) |

## Status state machine

Allowed transitions:

```
starting -> active | errored
active   -> paused | completed | errored
paused   -> active | completed | errored
```

`completed` and `errored` are terminal; further transitions return 409.

## Configuration

Read from environment variables (see `src/panakoes_session_manager/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret (auth signs from `AUTH_JWT_SECRET`; validators read `JWT_SECRET`, see `CONTRIBUTING.md`) |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `SESSIONS_TABLE_NAME` | `panakoes-streaming-sessions` | Provisioned by Terraform |
| `SESSION_TTL_SECONDS` | `28800` | 8 hours |
| `AWS_REGION` | `us-east-1` |  |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |

## Authentication

All endpoints except `/health` require `Authorization: Bearer <jwt>`.
The token must be HS256-signed with the shared secret (the auth
service signs from `AUTH_JWT_SECRET`; this validator reads `JWT_SECRET`,
see `CONTRIBUTING.md`) and carry the documented Auth-service payload
(`sub`, `email`, `jti`, `iss`, `aud`, `iat`, `exp`).

## DynamoDB schema

Table is provisioned out-of-band by Terraform (`infra/dev/data/`); see
the `streaming_sessions` resource. This service writes records with this
shape:

- `session_id` (hash key): `sess_<ulid>`
- `user_id`: owner subject from the JWT
- `status`: `starting | active | paused | completed | errored`
- `language`: optional ISO-639-1 hint, default `en`
- `gpu_instance_id`: optional, populated when the GPU is bound
- `created_at`, `updated_at`: ISO-8601 UTC strings
- `expires_at`: epoch seconds; DynamoDB TTL deletes the row after this

Owner scoping is enforced at the application layer: every read fetches by
`session_id`, then checks `user_id` matches the JWT subject; mismatches
return 404 (not 403) so we never leak the existence of other users'
sessions. Write paths use a `ConditionExpression` on `user_id` so a
race against a stolen id cannot corrupt another user's record.

## Audit events

| Action | When |
| --- | --- |
| `session.created` | `POST /sessions` succeeds |
| `session.status_changed` | `PATCH /sessions/{id}` updates status |
| `session.deleted` | `DELETE /sessions/{id}` succeeds |

`source_service` is `session-manager` for every event.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_session_manager.main:app --reload
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
docker build -t panakoes-session-manager .
```
