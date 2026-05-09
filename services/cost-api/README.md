# cost-api

Cost and budget API for the Panakoes admin dashboard (Tier 2). Reads
AWS Cost Explorer, caches results in DynamoDB, and exposes typed JSON
endpoints to the SvelteKit frontend.

This is the **Phase 0 skeleton**. Only `/health` is wired today. The
real surface area (`/cost/by-service`, `/cost/by-tenant`,
`/cost/forecast`, `/cost/anomalies`) lands in Phase 1 and Phase 2 of
`docs/design/tier-2-3-implementation-plan.md`.

## Layout

- `src/panakoes_cost_api/` - FastAPI app, settings, route modules (Phase 1+).
- `tests/unit/` - in-process tests with no external dependencies.
- `tests/integration/` - moto-backed AWS mocks via the `aws_mocks` fixture.

## Run locally

```bash
uv sync --frozen --group dev
uv run uvicorn panakoes_cost_api.main:app --reload
curl http://127.0.0.1:8000/health
```

## Test

```bash
uv run pytest          # full suite with coverage gate
uv run pytest -m unit  # fast loop
```

## Environment variables

| Var                        | Default                            | Purpose                                              |
|----------------------------|------------------------------------|------------------------------------------------------|
| `SERVICE_NAME`             | `cost-api`                         | Identifier used in logs and audit events.            |
| `LOG_LEVEL`                | `INFO`                             | structlog filtering threshold.                       |
| `AWS_REGION`               | `us-east-1`                        | AWS SDK region.                                      |
| `COST_CACHE_TABLE`         | `panakoes-dev-cost-cache`          | Cache of Cost Explorer results.                      |
| `TENANT_COST_ROLLUP_TABLE` | `panakoes-dev-tenant-cost-rollup`  | Per-tenant per-day cost aggregates.                  |
| `ALERT_STATE_TABLE`        | `panakoes-dev-alert-state`         | Anomaly-detector dedup state.                        |
| `AUDIT_LOG_TABLE`          | `panakoes-dev-audit-log`           | panakoes-audit DynamoDB sink.                        |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317`         | OTLP/gRPC collector endpoint (panakoes-otel default).|
| `OTEL_SDK_DISABLED`        | unset                              | Set to `true` to install NoOp providers (tests).     |
| `SERVICE_VERSION`          | unset                              | Resource attribute attached to every span.           |
| `DEPLOYMENT_ENVIRONMENT`   | `dev`                              | Resource attribute attached to every span.           |

## Related

- `infra/dev/admin-state/` - Terraform module that creates the four
  DynamoDB tables this service reads and writes.
- `docs/design/admin-dashboard-tier-2-3.md` - design doc.
- `docs/design/tier-2-3-implementation-plan.md` - phased plan.
