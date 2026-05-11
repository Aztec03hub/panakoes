# health-aggregator

Live data source for the Panakoes admin dashboard's Tier 1 health view.
Replaces the bundled static mock at
`services/admin/static/dashboard/health.json` and the per-service
detail mocks at `services/admin/static/dashboard/<name>.json`.

The admin SPA gates its switchover behind the `USE_LIVE_HEALTH_AGGREGATOR`
build-time flag (`services/admin/src/lib/config.ts`). Once this service
is deployed and gateway-wired, flipping the flag points the SPA at the
live endpoints with no code change.

## Endpoints

Mounted at the FastAPI root; the API Gateway forwards
`/v1/health-aggregator/{proxy+}` to this service with the prefix
stripped (ADR-038 (c+) shape).

- `GET /healthz`: liveness probe (no auth; for ECS / target-group checks).
- `GET /health`: snapshot for every monitored service. **Admin JWT required.**
- `GET /services/{name}`: per-service detail payload. **Admin JWT required.**

### `GET /health` response shape

```json
{
  "generated_at": "2026-05-08T12:00:00.000Z",
  "services": [
    {
      "service": "auth",
      "display_name": "Auth",
      "status": "healthy",
      "last_check": "2026-05-08T11:59:50.000Z"
    },
    {
      "service": "transcriber-stream",
      "display_name": "Transcriber (stream)",
      "status": "unhealthy",
      "last_check": "2026-05-08T11:59:50.000Z",
      "message": "running=0 desired=1"
    }
  ]
}
```

`status` is one of `healthy`, `unknown`, `unhealthy`. `message` is
omitted for healthy rows.

### Status decision matrix

For each monitored service the aggregator fans out three probes in
parallel via `asyncio.gather` and combines the results. First match
wins:

1. Any probe raised an unexpected exception -> `unknown`
2. ECS service not found -> `unknown`
3. ECS rollout in progress (`deployments != 1`) -> `unknown`
4. ECS `runningCount != desiredCount` -> `unhealthy`
5. Target group has unhealthy targets -> `unhealthy`
6. No log event within the heartbeat window -> `unknown`
7. Everything green -> `healthy`

The heartbeat window defaults to 5 minutes; tune via
`LOG_HEARTBEAT_WINDOW_SECONDS`.

## Layout

- `src/panakoes_health_aggregator/` - FastAPI app, settings, registry, aggregator, routes.
- `src/panakoes_health_aggregator/clients/` - thin async wrappers over boto3 ECS / ELBv2 / CloudWatch Logs.
- `tests/unit/` - in-process tests; fake boto3 clients.
- `tests/integration/` - moto-backed AWS mocks + FastAPI route tests.

## Run locally

```bash
uv sync --frozen --group dev
uv run uvicorn panakoes_health_aggregator.main:app --reload
curl http://127.0.0.1:8000/healthz
```

## Test

```bash
uv run pytest          # full suite with coverage gate (80%)
uv run pytest -m unit  # fast loop
```

## Environment variables

| Var                              | Default            | Purpose                                              |
|----------------------------------|--------------------|------------------------------------------------------|
| `SERVICE_NAME`                   | `health-aggregator`| Identifier used in logs and OTel resource attrs.     |
| `LOG_LEVEL`                      | `INFO`             | structlog filtering threshold.                       |
| `AWS_REGION`                     | `us-east-1`        | AWS SDK region.                                      |
| `ECS_CLUSTER`                    | `panakoes-dev`     | ECS cluster the registry's services run in.         |
| `LOG_HEARTBEAT_WINDOW_SECONDS`   | `300`              | How recent a log event must be to count as alive.   |
| `JWT_SECRET`                     | (required)         | HS256 shared secret with the auth service.          |
| `JWT_ISSUER`                     | (required)         | Expected `iss` claim.                                |
| `JWT_AUDIENCE`                   | (required)         | Expected `aud` claim.                                |
| `OTEL_EXPORTER_OTLP_ENDPOINT`    | `http://localhost:4317` | OTLP/gRPC collector endpoint.                  |
| `OTEL_SDK_DISABLED`              | unset              | `true` installs NoOp providers (tests).             |
| `SERVICE_VERSION`                | unset              | Resource attribute attached to every span.          |
| `DEPLOYMENT_ENVIRONMENT`         | `dev`              | Resource attribute attached to every span.          |

## Related

- `services/admin/src/lib/config.ts` - SPA feature flag `USE_LIVE_HEALTH_AGGREGATOR`.
- `services/admin/src/lib/api.ts` - SPA endpoint composition.
- `services/admin/static/dashboard/health.json` - static mock the live service replaces.
- `services/cost-api/` - canonical Python FastAPI service template this service mirrors.

## Follow-up (out of scope for v0.1)

- Terraform ECR repo + log group + IAM role + ECS task definition (separate PR).
- API Gateway proxy route `/v1/health-aggregator/{proxy+}` (separate PR).
- Bumping the SPA's `USE_LIVE_HEALTH_AGGREGATOR` default to `true` (separate PR after deploy validation).
- Populating `recent_logs`, `recent_errors`, and `metrics` on the detail endpoint from CloudWatch Logs Insights + ECS Container Insights.
- Replacing the hardcoded registry with tag-driven ECS service discovery.
