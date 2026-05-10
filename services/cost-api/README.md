# cost-api

Cost and budget API for the Panakoes admin dashboard (Tier 2). Reads
AWS Cost Explorer, caches results in DynamoDB, and exposes typed JSON
endpoints to the SvelteKit frontend.

Wired endpoints (Phase 1 + Phase 2):

- `GET /health`: liveness probe.
- `GET /api/v1/cost/by-service`: per-AWS-service spend for a `from`/`to` window.
- `GET /api/v1/cost/by-tenant`: per-tenant rollup for a `from`/`to` window.
- `GET /api/v1/cost/forecast?horizon_days=N`: CE-backed daily cost forecast for
  the next `N` days (`N` in `{7, 14, 30, 60, 90}`); each bucket carries the
  predicted spend plus a 95% prediction-interval lower / upper bound.
- `GET /api/v1/cost/anomalies`: cost-anomaly feed (active alert-state rows
  by default; pass `active_only=false` to additionally fetch fresh CE-detected
  anomalies for the last 30 days).

All `/api/v1/cost/*` routes require an admin JWT.

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
- `services/cost-rollup-aggregator/` - nightly Lambda that populates
  `panakoes-dev-tenant-cost-rollup` from AWS Cost Explorer. The
  by-tenant route returns empty rows until this populator has run at
  least once for the requested window.
- `docs/design/admin-dashboard-tier-2-3.md` - design doc.
- `docs/design/tier-2-3-implementation-plan.md` - phased plan.
