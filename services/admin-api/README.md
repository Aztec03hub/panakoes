# admin-api

Admin lifecycle API for the Panakoes admin dashboard (Tier 3). Hosts
the safety pattern for every dangerous lifecycle operation: typed
confirmation string, idempotency-by-key, audit-before-AND-after, and
step-up MFA enforcement.

Phase 1 (3 ops) and Phase 2 (5 more ops) of the lifecycle dashboard
are shipped. Phase 3 (audit-log read view) is also live. The phased
plan lives in `docs/design/tier-2-3-implementation-plan.md`.

## Tier 3 lifecycle operations

Every operation enforces the same safety pattern (typed confirmation
+ idempotency-key + step-up MFA + audit-before-AND-after; ADR-032)
and follows the response semantics in ADR-033 (protocol failures
return 200-with-status-failed; only transport / auth / validation
errors raise 4xx).

| Operation                | Endpoint                                                              | Confirmation                       |
|--------------------------|-----------------------------------------------------------------------|------------------------------------|
| terminate-session        | `POST /api/v1/admin/sessions/{session_id}/terminate`                  | `TERMINATE <session_id>`           |
| force-fail-ingestion     | `POST /api/v1/admin/ingestions/{ingestion_id}/force-fail`             | `FAIL <ingestion_id>`              |
| block-user-sessions      | `POST /api/v1/admin/users/{user_id}/block-sessions`                   | `BLOCK USER <user_id>`             |
| block-tenant             | `POST /api/v1/admin/tenants/{tenant_id}/block`                        | `BLOCK TENANT <tenant_id>`         |
| revoke-api-key           | `POST /api/v1/admin/api-keys/{api_key_id}/revoke`                     | `REVOKE KEY <api_key_id>`          |
| kill-streaming-session   | `POST /api/v1/admin/streaming-sessions/{session_id}/kill`             | `KILL STREAM <session_id>`         |
| kill-batch-job           | `POST /api/v1/admin/batch-jobs/{job_id}/kill`                         | `KILL JOB <job_id>`                |
| force-billing-recompute  | `POST /api/v1/admin/tenants/{tenant_id}/force-billing-recompute`      | `RECOMPUTE BILLING <tenant_id>`    |

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
| `TENANTS_TABLE`              | `panakoes-dev-tenants`             | Read/written by block-tenant + force-billing-recompute (table not yet provisioned in `infra/dev/data/`; operator follow-up Terraform PR required). |
| `API_KEYS_TABLE`             | `panakoes-dev-api-keys`            | Read/written by revoke-api-key (table not yet provisioned in `infra/dev/data/`; operator follow-up Terraform PR required). |
| `EVENTS_BUS_NAME`            | `panakoes-dev`                     | EventBridge custom bus used by kill-streaming-session + force-billing-recompute. |
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
