# services/notification

Notification microservice for Panakoes. Sends transactional emails via
Amazon SES and outbound HTTP webhooks, and exposes a per-user
notification history backed by DynamoDB.

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET  | `/health`                      | no  | Liveness probe |
| POST | `/notify/email`                | yes | Render a Jinja2 template, send via SES |
| POST | `/notify/webhook`              | yes | POST a JSON payload to an HTTPS URL with retry + backoff |
| GET  | `/notifications`               | yes | List the caller's notifications (paginated, default 25, max 100) |
| GET  | `/notifications/{ulid}`        | yes | Fetch one of the caller's notifications |

All endpoints except `/health` require `Authorization: Bearer <jwt>`.
The token must be HS256-signed with `JWT_SECRET` and carry the
documented Auth-service payload (`sub`, `email`, `jti`, `iss`, `aud`,
`iat`, `exp`). Service-to-service tokens (with `actor_type: "service"`
in the payload) are also accepted.

## Configuration

Read from environment variables (see
`src/panakoes_notification/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `SES_FROM_ADDRESS` | `no-reply@panakoes.com` | Verified SES sender identity |
| `DDB_NOTIFICATION_TABLE` | `panakoes-notification` | Provisioned by Terraform |
| `AWS_REGION` | `us-east-1` |  |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint (ADOT in prod) |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` in tests + offline dev to wire NoOp providers |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |
| `DEPLOYMENT_ENVIRONMENT` | `dev` | Stamped onto the `deployment.environment` resource attribute |

## Templates

Templates live under `services/notification/templates/` and follow a
two-file convention per logical template name:

- `<name>.txt.j2`: required; rendered with autoescape OFF (plaintext).
- `<name>.html.j2`: optional; rendered with autoescape ON when present;
  delivered as the HTML alternative of a multipart/alternative email.

Two templates ship today: `welcome` and `summary_ready`. Add a new
template by dropping the `.txt.j2` (and optional `.html.j2`) into
`services/notification/templates/` and pointing `template:` at its
basename in the request body.

## DynamoDB schema

The table is provisioned out-of-band by Terraform. This service writes
records with this shape:

- `pk = "USER#" + user_id`
- `sk = "NOTIFY#" + ulid` (ULIDs sort lexicographically by time)
- attributes: `notification_id`, `user_id`, `channel`
  (`email` | `webhook`), `status` (`sent` | `failed`), `target`,
  `subject`, `template`, `error`, `attempts`, `created_at`

Listing is descending-by-time (`ScanIndexForward=False`) so the newest
notifications surface first.

## Webhook delivery

`POST /notify/webhook` POSTs the JSON payload to the URL with three
attempts and exponential backoff (base 1s, doubling). Network errors
and 5xx responses retry; 4xx responses do not (the receiver said no
explicitly). HTTPS is required at validation time; `http://` is
rejected with 400 so private-network exfiltration paths stay closed.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_notification.main:app --reload
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
docker build -t panakoes-notification .
```
