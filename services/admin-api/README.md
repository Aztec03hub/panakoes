# admin-api

Admin lifecycle API for the Panakoes admin dashboard (Tier 3). Hosts
the safety pattern for every dangerous lifecycle operation: typed
confirmation string, idempotency-by-key, audit-before-AND-after, and
step-up MFA enforcement.

This is the **Phase 0 skeleton**. Only `/health` is wired today. Phase
1 lands the safety pattern itself plus the first three lifecycle
operations (terminate session, revoke API credentials, force password
reset). Phase 2 lands five additional ops on the proven pattern. Phase
3 lands the audit-log read view. See
`docs/design/tier-2-3-implementation-plan.md`.

## Layout

- `src/panakoes_admin_api/` - FastAPI app, settings, route modules (Phase 1+).
- `tests/unit/` - in-process tests with no external dependencies.
- `tests/integration/` - moto-backed AWS mocks via the `aws_mocks` fixture.

## Run locally

```bash
uv sync --frozen --group dev
uv run uvicorn panakoes_admin_api.main:app --reload
curl http://127.0.0.1:8000/health
```

## Test

```bash
uv run pytest          # full suite with coverage gate
uv run pytest -m unit  # fast loop
```

## Environment variables

| Var                          | Default                            | Purpose                                              |
|------------------------------|------------------------------------|------------------------------------------------------|
| `SERVICE_NAME`               | `admin-api`                        | Identifier used in logs and audit events.            |
| `LOG_LEVEL`                  | `INFO`                             | structlog filtering threshold.                       |
| `AWS_REGION`                 | `us-east-1`                        | AWS SDK region.                                      |
| `LIFECYCLE_STATE_TABLE`      | `panakoes-dev-lifecycle-state`     | Tier 3 idempotency + result envelope.                |
| `AUDIT_LOG_TABLE`            | `panakoes-dev-audit-log`           | panakoes-audit DynamoDB sink.                        |
| `STREAMING_SESSIONS_TABLE`   | `panakoes-dev-streaming-sessions`  | Read by the terminate-session lifecycle op.          |
| `INGESTION_TABLE`            | `panakoes-dev-ingestion`           | Read by tenant-data-purge and force-fail-ingestion.  |
| `OTEL_EXPORTER_OTLP_ENDPOINT`| `http://localhost:4317`            | OTLP/gRPC collector endpoint (panakoes-otel default).|
| `OTEL_SDK_DISABLED`          | unset                              | Set to `true` to install NoOp providers (tests).     |
| `SERVICE_VERSION`            | unset                              | Resource attribute attached to every span.           |
| `DEPLOYMENT_ENVIRONMENT`     | `dev`                              | Resource attribute attached to every span.           |

## Related

- `infra/dev/admin-state/` - Terraform module that creates the
  `panakoes-dev-lifecycle-state` table. The `Tier3ActionIndex` GSI on
  `panakoes-dev-audit-log` lives in `infra/dev/data/`.
- `docs/design/admin-dashboard-tier-2-3.md` - design doc.
- `docs/design/tier-2-3-implementation-plan.md` - phased plan.
