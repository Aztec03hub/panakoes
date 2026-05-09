# Admin Dashboard Tier 2 + Tier 3 implementation plan

> **Status:** Proposed. Companion to [`admin-dashboard-tier-2-3.md`](./admin-dashboard-tier-2-3.md) (the design doc). The design says **what** to build and **why**; this plan says **how** to build it, **in what order**, and **how to know each phase is done**.
>
> **Audience:** the maintainer (or any senior contributor who's read CLAUDE.md and the design doc). Concrete enough that Day 1 of any phase is "open this file and start writing the test described in section X.Y.Z."

---

## Table of contents

1. [Scope and assumptions](#scope-and-assumptions)
2. [Pre-flight prerequisites](#pre-flight-prerequisites)
3. [Phase 0: Foundation (3 days)](#phase-0-foundation-3-days)
4. [Phase 1: Tier 2.1 - cost-api skeleton + by-service breakdown (1 week)](#phase-1-tier-21--cost-api-skeleton--by-service-breakdown-1-week)
5. [Phase 2: Tier 2.2 - by-tenant + forecast + anomalies (1 week)](#phase-2-tier-22--by-tenant--forecast--anomalies-1-week)
6. [Phase 3: Tier 3.1 - admin-api skeleton + first three ops (1.5 weeks)](#phase-3-tier-31--admin-api-skeleton--first-three-ops-15-weeks)
7. [Phase 4: Tier 3.2 - remaining lifecycle operations (1.5 weeks)](#phase-4-tier-32--remaining-lifecycle-operations-15-weeks)
8. [Phase 5: Tier 3.3 - audit log read view (0.5 week)](#phase-5-tier-33--audit-log-read-view-05-week)
9. [Cross-cutting concerns](#cross-cutting-concerns)
10. [Operational handoff](#operational-handoff)
11. [Risk register](#risk-register)
12. [Cost projection](#cost-projection)
13. [Sequencing summary + Gantt](#sequencing-summary--gantt)

---

## Scope and assumptions

This plan implements the design in [`admin-dashboard-tier-2-3.md`](./admin-dashboard-tier-2-3.md). Read that first; the WHY for each architectural decision lives there. This document does not re-litigate decisions; it implements them.

**In scope:**
- Two new Python microservices: `services/cost-api` (Tier 2 backend) and `services/admin-api` (Tier 3 backend).
- Five new DynamoDB tables (cost cache, audit-extended, tenant-cost-rollup, lifecycle-state, alert-state) and one extension to the existing `panakoes-audit` table.
- Twelve new SvelteKit dashboard routes under `/dashboard/cost/*` and `/dashboard/lifecycle/*`.
- Two new IAM service roles, six new IAM policies, three new CloudWatch alarm sets, two new SNS topic subscriptions, one new Step Functions state machine (the lifecycle-confirmation flow).
- Test coverage to project standards: 80% on services, 100% on admin-api (it sits adjacent to the audit/billing path).
- Documentation: per-service READMEs, per-route OpenAPI specs, three new runbook entries, two new ADRs.

**Explicitly out of scope:**
- Tier 4 (deep observability, log search, distributed-trace UI). Deferred per `SCOPE.md`.
- Multi-environment promotion (staging, prod). Implementation lands in `dev` only; staging/prod follow the same pattern when those environments exist.
- A standalone web frontend bundle for cost data. Tier 2 surfaces lives inside the existing admin SvelteKit app; no new frontend service.
- AWS Cost and Usage Reports (CUR) integration. Cost Explorer is sufficient for Tier 2 coverage; CUR is a Phase 2 enhancement when historical-month-level analysis is needed.

**Calendar assumption:** roughly six weeks of focused solo work end-to-end. With normal distractions, double that. Tier 2 ships before Tier 3 because Tier 2 is fully read-only (lower blast radius) and validates the data plumbing patterns Tier 3 reuses.

**Definition of done (overall):** every Tier 2 page loads against real AWS data with no manual workarounds; every Tier 3 operation has been exercised end-to-end against the dev environment with a real audit-log entry produced; the operational handoff section's runbook entries are written, linked, and tested by the maintainer running each one once.

---

## Pre-flight prerequisites

Items that must be true before Phase 0 starts. Each is either already done (✓) or a one-time setup task. If any is not done, do it as a zero-th step before the phased plan begins.

| Item | Status | Notes |
|---|---|---|
| AWS account bootstrap | ✓ | `infra/bootstrap/`, root MFA, IAM admin, CloudTrail. Already in main. |
| Terraform remote state | ✓ | S3 + KMS-encrypted + DynamoDB lock. `infra/bootstrap/`. |
| AWS Activate Founders credits | pending | Phil to submit. Cost Explorer is **not free** past 1000 queries/month. The credit budget materially affects the early-phase cost projection. |
| Cost Explorer enabled | pending | One-click in AWS Billing console. Required by Phase 1. |
| AWS Budgets configured | pending | At least one monthly budget set, even if nominal. Required by Phase 2. |
| `panakoes-test-helpers` library | ✓ | jwt + aws + factories. Used in every phase's tests. |
| `panakoes-otel` Python library | ✓ | OTel instrumentation. Both new services consume it. |
| `panakoes-audit` library | ✓ | Audit logging. Tier 3 consumes it heavily. |
| `panakoes-auth-client` library | ✓ | JWT validation + step-up MFA verification. Tier 3 consumes it. |
| Better-Auth step-up MFA route | ✓ | PR #89 wired the route + claim. Tier 3 verifies the claim. |
| Admin Tier 1 dashboard | ✓ | PR #56 (svelte skeleton + health page). Tier 2/3 routes layer on top. |
| Local dev stack | ✓ | `make dev-up` provisions Postgres + DynamoDB-local + LocalStack. Phase 0 verifies cost-cache table works in LocalStack. |

---

## Phase 0: Foundation (3 days)

Goal: stand up the shared scaffolding both new services need. No user-visible features ship in Phase 0; this is plumbing.

### Day 0.1: Terraform - DynamoDB tables + IAM scaffolding

**Files to create:**

```
infra/dev/cost-cache/
  main.tf            # DynamoDB table panakoes-dev-cost-cache (PAY_PER_REQUEST, TTL on `expires_at`)
  variables.tf
  outputs.tf
  providers.tf
  README.md

infra/dev/admin-api-tables/
  main.tf            # 3 tables: panakoes-dev-lifecycle-state, panakoes-dev-tenant-cost-rollup, panakoes-dev-alert-state
  variables.tf
  outputs.tf
  providers.tf
  README.md
```

Each table:
- `PAY_PER_REQUEST` billing mode (variable, low-fixed cost).
- KMS-encrypted with `dev/observability/`'s shared CMK (consume via `terraform_remote_state` with `try()` fallback).
- Point-in-time recovery enabled.
- Deletion protection ON.
- Standard tag set (`Project`, `Environment`, `Service`, `ManagedBy`).

**TTL specifics:**
- `cost-cache`: TTL on `expires_at` (Unix epoch seconds). Cache entries auto-purge after 1 hour for daily-granularity, 24 hours for monthly-granularity.
- `tenant-cost-rollup`: no TTL (audit-style retention).
- `lifecycle-state`: TTL on `expires_at` for ephemeral entries (in-flight operation locks); permanent entries (cumulative state) have no TTL.
- `alert-state`: no TTL (alert history retention).

**Audit table extension:** add a GSI to the existing `panakoes-audit` table keyed on `(tier3_action, timestamp)` so `/dashboard/lifecycle/audit` can query Tier 3 events specifically. New GSI in `infra/dev/data/main.tf`; no schema change to existing items.

**Acceptance:**
- `terraform plan` clean against existing state.
- `terraform apply` produces the four tables and the audit GSI.
- A test write/read against each table from the local LocalStack stack succeeds.

### Day 0.2: Service templates

**Files to create:**

```
services/cost-api/                      # Copy from services/_template/, rename
  README.md
  pyproject.toml
  Dockerfile
  src/panakoes_cost_api/__init__.py
  src/panakoes_cost_api/main.py         # FastAPI lifespan + OTel wiring
  src/panakoes_cost_api/config.py       # Pydantic Settings: AWS_REGION, CACHE_TABLE, etc.
  src/panakoes_cost_api/auth.py         # admin-role JWT validation via panakoes-auth-client
  src/panakoes_cost_api/routes/
    __init__.py
    health.py                            # /health -> 200 OK
  tests/
    conftest.py
    integration/test_health.py

services/admin-api/                     # Same shape; different module name
  ...similar layout...
```

**Key choices:**
- Both services use FastAPI + Uvicorn (consistent with `ingestion-api`, `notification`, `query-api`, `summarization`, `billing`).
- Both inherit `panakoes-otel`, `panakoes-audit`, `panakoes-auth-client`, `panakoes-middleware`, `panakoes-models` via local path-deps in pyproject.toml.
- Both have `OTEL_SDK_DISABLED=true` set in conftest so test runs don't open exporter sockets.
- Both define `[tool.coverage.run]` to require 80% (cost-api) and 100% (admin-api) coverage.
- Both have a `Dockerfile` that copies the path-dep libraries into the image (mirroring the `event-router` and `billing` patterns).

**Acceptance:**
- `cd services/cost-api && uv sync --group dev && pytest -m unit` passes (zero tests yet, but pytest discovers and reports correctly).
- `cd services/cost-api && pytest -m integration` passes the health-endpoint test.
- Same for `admin-api`.
- Local `docker build` produces an image for each.

### Day 0.3: Frontend scaffolding + types library

**Files to create:**

```
services/admin/src/lib/api/
  cost.ts            # typed client for /api/v1/cost/* endpoints
  lifecycle.ts       # typed client for /api/v1/lifecycle/* endpoints

services/admin/src/lib/types/
  cost.ts            # mirrors Pydantic models from cost-api
  lifecycle.ts       # mirrors Pydantic models from admin-api

services/admin/src/routes/dashboard/cost/
  +layout.svelte    # navigation tabs for cost subpages
  +page.svelte      # /dashboard/cost (Tier 2 home, placeholder)

services/admin/src/routes/dashboard/lifecycle/
  +layout.svelte    # navigation tabs for lifecycle subpages
  +page.svelte      # /dashboard/lifecycle (Tier 3 home, placeholder)
```

**Why ship the placeholder pages now:** the SvelteKit router needs the routes to exist before the dashboard navigation can link to them; landing them as 200-OK placeholders means subsequent phases just fill the placeholder, not bootstrap a route.

**Add to `services/admin/src/routes/+layout.svelte`:** new navigation entries for "Cost" and "Lifecycle" pointing at the new routes. Hidden behind a `feature.cost_tab_enabled` and `feature.lifecycle_tab_enabled` boolean (initially false in dev, flip true in Phase 1+).

**Acceptance:**
- `pnpm typecheck` clean across `services/admin`.
- `pnpm test` clean (existing tests still pass).
- Manual smoke: `pnpm dev`, navigate to `/dashboard/cost` and `/dashboard/lifecycle`, see "Coming soon" copy without errors in the browser console.

### Phase 0 deliverables

- 4 new DynamoDB tables provisioned in dev.
- 1 GSI added to the existing audit table.
- 2 new Python service skeletons (`cost-api`, `admin-api`) building, testing, and Docker-imaging cleanly.
- 6 new SvelteKit files (typed clients, types, layouts, placeholder pages) + 2 navigation entries.

### Phase 0 rollback

If anything goes wrong, the rollback is straightforward because no user-visible behavior changes:

- Terraform: `terraform destroy -target` on the new tables and `terraform apply` to revert the GSI addition.
- Service skeletons: `git revert` the merge commits; ECR images can stay (they're immutable; nothing references them yet).
- Frontend: `git revert` the route-creating commits; the navigation entries fail-closed (feature flag false).

---

## Phase 1: Tier 2.1 - cost-api skeleton + by-service breakdown (1 week)

Goal: surface "AWS cost broken down by service for a date range" in the dashboard. Validates the data-plumbing pattern that all of Tier 2 reuses.

### Day 1.1-1.2: Cost Explorer client + cache layer (TDD)

**Files to create / modify:**

```
services/cost-api/src/panakoes_cost_api/
  cost_explorer.py     # async wrapper around boto3 ce client
  cache.py             # DynamoDB cache get/put with TTL
  models.py            # Pydantic: CostBreakdown, CostByService, DateRange, CacheKey

services/cost-api/tests/
  unit/test_cost_explorer.py    # mocks ce.get_cost_and_usage; asserts response shape
  unit/test_cache.py             # uses moto for DynamoDB; asserts TTL math
  unit/test_cache_keys.py        # asserts cache_key generation is deterministic and stable
  integration/test_cost_breakdown.py  # uses moto for both ce and dynamodb
```

**TDD sequence (write tests first):**

1. `test_cache_key_generation`: given `(date_range, group_by, service_filter)` produces the same cache key every time, regardless of dict ordering.
2. `test_cache_miss_returns_none`: `cache.get(missing_key)` returns `None` cleanly.
3. `test_cache_put_then_get_roundtrip`: `cache.put(key, value)` followed by `cache.get(key)` returns the value.
4. `test_cache_expires_at_is_one_hour_default`: cache entries write `expires_at = now + 3600` by default.
5. `test_cost_explorer_get_cost_by_service`: mocked ce returns sample response; client parses into `CostBreakdown`.
6. `test_cost_explorer_handles_throttle`: client retries with exponential backoff on `ThrottlingException`.
7. `test_cost_explorer_handles_invalid_date_range`: client raises `InvalidDateRangeError`, not a generic AWS exception.
8. `test_cache_or_fetch_uses_cache_on_hit`: cache hit means CE is never called.
9. `test_cache_or_fetch_falls_back_to_ce_on_miss`: cache miss means CE is called and result is cached before returning.

**Acceptance:**
- All 9 unit tests pass.
- 80%+ coverage on `cost_explorer.py` and `cache.py`.
- Integration test against LocalStack-backed DynamoDB and moto-mocked CE succeeds.

### Day 1.3: First endpoint (TDD)

**Files to create / modify:**

```
services/cost-api/src/panakoes_cost_api/
  routes/cost.py       # GET /api/v1/cost/by-service?from=YYYY-MM-DD&to=YYYY-MM-DD
  main.py              # mount the cost router

services/cost-api/tests/
  integration/test_cost_routes.py
```

**Endpoint contract:**

```
GET /api/v1/cost/by-service?from=2026-04-01&to=2026-05-01
Authorization: Bearer <admin-role JWT>

200 OK
{
  "from": "2026-04-01",
  "to": "2026-05-01",
  "currency": "USD",
  "services": [
    {"service": "Amazon Elastic Compute Cloud - Compute", "cost_cents": 1234, "percent_of_total": 12.3},
    ...
  ],
  "total_cents": 10042,
  "cache_hit": true,
  "queried_at": "2026-04-30T14:32:18Z"
}

401 if no/invalid JWT
403 if non-admin
400 if from > to or invalid date format
502 if CE returns an unrecoverable error
```

**TDD sequence:**

1. `test_unauth_returns_401`: no auth header, 401.
2. `test_non_admin_returns_403`: valid JWT, non-admin role, 403.
3. `test_invalid_date_returns_400`: from > to, 400.
4. `test_happy_path_cache_miss_returns_data`: admin JWT, valid range, cache miss, returns sorted-by-cost services.
5. `test_happy_path_cache_hit_returns_data_with_cache_hit_true`: same but cache pre-populated, returns `cache_hit: true`.
6. `test_ce_error_returns_502`: CE mock returns AccessDenied, response is 502 (not 500).

**Acceptance:**
- All 6 endpoint tests pass.
- 80%+ coverage on `routes/cost.py`.
- Manual smoke: deploy to LocalStack, hit the endpoint with a fixture JWT, see cost data.

### Day 1.4: Frontend by-service page

**Files to create / modify:**

```
services/admin/src/routes/dashboard/cost/by-service/
  +page.svelte         # the page itself
  +page.ts             # SvelteKit load fn fetches from cost-api

services/admin/src/lib/api/cost.ts   # add fetchCostByService(from, to)

services/admin/src/lib/components/cost/
  CostByServiceChart.svelte    # bar chart via Layer Chart (or Recharts)
  CostByServiceTable.svelte    # accessible table fallback for screen readers / dense data

services/admin/tests/
  cost-by-service-page.test.ts
  cost-api-client.test.ts
```

**TDD sequence:**

1. `test_api_client_returns_typed_response`: msw mocks the cost-api response, the client returns a typed `CostByServiceResponse`.
2. `test_api_client_handles_401_by_redirecting_to_login`: 401 from API → redirect.
3. `test_page_renders_loading_state`: while load fn pending, skeleton shows.
4. `test_page_renders_data_table`: with sample data, table shows top services and total.
5. `test_page_chart_aria_label_describes_data`: accessibility test asserts the chart has a meaningful aria-label per OWASP+WCAG patterns.
6. `test_page_handles_api_error`: API returns 502, page renders an error message (not a blank screen).

**Date range UX:** default to "current calendar month so far"; offer presets (last 7 days, last 30 days, last billing cycle) plus a custom range picker.

**Acceptance:**
- All 6 frontend tests pass.
- Coverage 80%+ on the page and api-client modules.
- Manual smoke against the local stack (LocalStack-backed DynamoDB cache, moto-mocked CE) loads the page and shows mock data.

### Day 1.5: Terraform - IAM + service deployment

**Files to create / modify:**

```
infra/dev/iam/main.tf          # new role: panakoes-dev-cost-api-task
                                 # policies: ce:GetCostAndUsage, dynamodb:GetItem/PutItem on cost-cache
infra/dev/ecr/main.tf          # new ECR repo: panakoes-cost-api
.github/workflows/deploy-cost-api.yml   # builds + pushes Docker image on merge to main
```

**Iterate:** the deployment workflow is dry-run-only in Phase 1 (builds the image, verifies it loads, but does NOT deploy to ECS yet because we don't have ECS modules until Tier 2 phase 2 lands). The workflow is wired now so deployment is a one-line change later.

**Acceptance:**
- Terraform `apply` clean.
- ECR repo exists.
- Workflow runs on a sample PR + on merge to main; output shows successful image build.

### Phase 1 deliverables

- One end-to-end vertical slice: AWS CE → cost-api → DynamoDB cache → SvelteKit dashboard → user sees cost-by-service breakdown.
- Test coverage: 80%+ on cost-api, 80%+ on admin frontend changes.
- Terraform IAM + ECR resources for cost-api in place.
- Docker image build pipeline wired (dry-run deploy until Phase 2).

### Phase 1 rollback

- Frontend: `feature.cost_tab_enabled = false` in admin config; the cost tab disappears, page is no-op.
- Backend: stop the cost-api ECS task (or never start it). Cache table can stay populated; cost is < $0.10/month at the cache size we'll generate.
- Terraform: `terraform destroy` on the cost-api specific resources if a true revert is needed.

---

## Phase 2: Tier 2.2 - by-tenant + forecast + anomalies (1 week)

Goal: round out Tier 2 with the three remaining pages. Reuses all the Phase 1 plumbing.

### Day 2.1: Per-tenant cost rollup (TDD)

**Background:** AWS CE doesn't natively know what a "tenant" is; it knows AWS resource tags. We tag resources with `Tenant=<id>` where applicable, but most fixed costs (NAT, monitoring, KMS) aren't per-tenant taggable. Resolution: a hybrid model.

- **Variable per-tenant costs:** transcription minutes, summarization tokens. Computed by `cost-api` from the audit log + per-backend cost estimator (see Transcriber design doc, "Cost estimation" section).
- **Fixed costs (NAT, monitoring, KMS, etc.):** amortized across all active tenants pro-rata to active-minute usage in the period. Computed by `cost-api` as part of the rollup.
- **Stripe revenue (the inverse side of cost):** outside the scope of this service. Cost-api surfaces costs only; the future billing-api surfaces revenue.

**Files to create / modify:**

```
services/cost-api/src/panakoes_cost_api/
  tenant_rollup.py      # combines variable (audit-log-derived) + fixed (CE-derived + amortized) into per-tenant rows
  routes/cost.py        # add GET /api/v1/cost/by-tenant
services/cost-api/tests/
  unit/test_tenant_rollup_variable_costs.py
  unit/test_tenant_rollup_fixed_amortization.py
  unit/test_tenant_rollup_combined.py
  integration/test_by_tenant_endpoint.py
```

**TDD sequence (8 tests):**

1. `test_variable_cost_zero_for_tenant_with_no_activity`.
2. `test_variable_cost_proportional_to_transcription_minutes`.
3. `test_fixed_cost_amortization_pro_rata_by_active_minutes`.
4. `test_fixed_cost_zero_for_period_with_no_active_tenants`.
5. `test_combined_rollup_sums_correctly`.
6. `test_rollup_caches_to_tenant_cost_rollup_table`.
7. `test_endpoint_filters_to_admin_role_caller`.
8. `test_endpoint_supports_per_tenant_filter_query_param`.

### Day 2.2: Forecast page

**Approach:** AWS CE Forecast API gives us an end-of-month projection. Cost-api passes through the CE forecast plus our own simple linear-extrapolation as a sanity check (for short months, CE Forecast can be wildly off).

**Files:**
- `services/cost-api/src/panakoes_cost_api/forecast.py`
- `services/cost-api/src/panakoes_cost_api/routes/cost.py` (add `GET /api/v1/cost/forecast`)
- `services/admin/src/routes/dashboard/cost/forecast/+page.svelte`
- 5 unit tests + 1 integration test (forecast accuracy, fallback when forecast unavailable, etc.)

### Day 2.3: Anomaly alerts page

**Background:** AWS Budgets fires alerts to SNS topic `panakoes-dev-system-alerts` when 50%, 80%, or 100% (forecast) thresholds cross. The notification service consumes that topic and writes alerts to DynamoDB (`panakoes-dev-alert-state`). Cost-api reads from that table for the dashboard.

**Files:**
- `services/cost-api/src/panakoes_cost_api/alerts.py`
- `services/cost-api/src/panakoes_cost_api/routes/cost.py` (add `GET /api/v1/cost/anomalies`)
- `services/notification/src/panakoes_notification/budget_alert_handler.py` (NEW: handles SNS messages from Budgets; extends existing notification service)
- `services/admin/src/routes/dashboard/cost/anomalies/+page.svelte`
- Terraform: `infra/dev/budgets/main.tf` (NEW: provisions monthly budgets with SNS notifications)

### Day 2.4-2.5: Frontend pages + integration

The three new dashboard pages (`/dashboard/cost/by-tenant`, `/dashboard/cost/forecast`, `/dashboard/cost/anomalies`) follow the same shape as Phase 1's by-service page. Each gets:

- A typed API client function in `services/admin/src/lib/api/cost.ts`.
- A new Svelte component in `services/admin/src/lib/components/cost/`.
- A new route + load fn.
- 4-6 frontend tests per page.

**Acceptance for Phase 2:**
- All four cost pages load against the local stack and the dev AWS account.
- Forecast page shows both the CE forecast AND our linear-extrapolation sanity check; flags when they diverge by more than 25%.
- Anomaly page shows the past 30 days of budget alerts.
- Per-tenant breakdown reconciles to within $0.01 of the total cost from by-service breakdown for the same period (tested by an integration test that asserts the equality).

### Phase 2 rollback

- Per-page feature flags (`feature.cost_by_tenant_enabled`, etc.) so any single page can be disabled.
- Budgets Terraform module is independent of all other infra; `terraform destroy -target` on it removes the budgets and their alerts without affecting any service.
- The `notification` service's `budget_alert_handler` is opt-in via env var `PANAKOES_BUDGET_ALERT_HANDLER_ENABLED`; defaults to false in case it ships before Phase 2 frontend is ready.

---

## Phase 3: Tier 3.1 - admin-api skeleton + first three ops (1.5 weeks)

Goal: ship the lifecycle-control discipline (step-up MFA + typed confirmation + audit-before-AND-after) for the three lowest-blast-radius operations, validating the safety pattern before Phase 4 layers on the higher-blast-radius ones.

### Day 3.1: Lifecycle command catalog + step-up gate (TDD)

**Files to create:**

```
services/admin-api/src/panakoes_admin_api/
  commands.py          # base LifecycleCommand class + registry
  step_up_gate.py      # FastAPI dependency that verifies step-up MFA claim
  audit.py             # audit-before-AND-after wrapper
  models.py            # Pydantic: LifecycleRequest, LifecycleOutcome, LifecycleAuditEntry
  routes/
    __init__.py
    lifecycle.py
  commands/
    __init__.py
    drain_streaming_service.py
    terminate_gpu_session.py
    rotate_jwt_signing_key.py

services/admin-api/tests/
  unit/test_step_up_gate_rejects_old_claim.py
  unit/test_step_up_gate_accepts_fresh_claim.py
  unit/test_audit_wrapper_writes_before.py
  unit/test_audit_wrapper_writes_after_on_success.py
  unit/test_audit_wrapper_writes_after_on_failure.py
  unit/test_command_registry_lookup.py
  unit/test_command_typed_confirmation_string_match.py
```

**TDD sequence (key tests):**

1. `test_step_up_gate_rejects_request_without_step_up_claim`: request with admin JWT but no `mfa_step_up_at` claim → 403.
2. `test_step_up_gate_rejects_step_up_claim_older_than_5_minutes`: claim 6 minutes old → 403.
3. `test_step_up_gate_accepts_step_up_claim_within_5_minutes`: claim 4 minutes old → request proceeds.
4. `test_audit_writes_intent_before_command_runs`: audit table has an `intent` row before the command's body executes.
5. `test_audit_writes_outcome_after_command_runs`: audit table has an `outcome` row after.
6. `test_audit_writes_outcome_failure_when_command_raises`: command raising → audit `outcome` row records the failure.
7. `test_typed_confirmation_string_must_match`: request with `confirmation: "DRAIN streaming/auth-prod"` for a `drain` command targeting `streaming/auth-dev` → 400 (target mismatch).

**Acceptance:**
- All 7 tests pass.
- 100% coverage on `step_up_gate.py` and `audit.py` (project rule for security-adjacent code).
- 80%+ coverage on commands/.

### Day 3.2: First operation - drain a streaming service

**Files:**

```
services/admin-api/src/panakoes_admin_api/commands/
  drain_streaming_service.py
services/admin-api/tests/integration/
  test_drain_streaming_service.py
```

**Operation specifics:**
- Input: target service name (e.g. `streaming-transcriber`), reason string (free-text, audited).
- Action: write a `drain=true` flag to the service's runtime-config DynamoDB table; the service polls this flag and stops accepting new sessions.
- Reversibility: a corresponding `undrain` command (or PUT to the same flag with `drain=false`).
- Audit: actor, action, target, timestamp, source IP, reason, outcome.

**Tests (5):**
1. Happy path: drain a service, runtime-config table has the flag, audit log has both rows.
2. Idempotency: drain a service that's already drained → no-op success, audit reflects "no change."
3. Authorization: non-admin → 403; admin without step-up → 403.
4. Confirmation mismatch: confirmation string doesn't match → 400.
5. Failure handling: simulate DynamoDB write failure → audit reflects the failure, command returns 500.

### Day 3.3: Second operation - terminate a GPU session

Same shape as drain but mutates the GPU spawner's session state. Tests follow the same five-test pattern.

### Day 3.4: Third operation - force-rotate JWT signing key

**This is the highest-blast-radius of the three** (it invalidates ALL active sessions). Extra care:
- Two-step typed confirmation: type the operation name AND a random nonce displayed in the modal.
- Audit log records the previous key fingerprint AND the new key fingerprint (without revealing the key material).
- Post-operation, the audit log surfaces the count of sessions that were active at rotation time so the operator knows the user-visible impact.

**Tests (8 - extra cases for the higher-risk operation):**
1-5: Same five as drain.
6. Pre-rotation session count is captured in audit.
7. Post-rotation, an authenticated request with the old JWT returns 401.
8. Re-rotation within 60 seconds is blocked (anti-thrash protection).

### Day 3.5-3.6: Frontend - three lifecycle pages

```
services/admin/src/routes/dashboard/lifecycle/
  drain/+page.svelte
  sessions/+page.svelte         # GPU session terminator
  keys/+page.svelte              # JWT key rotation
```

Each page:
- Lists current state of the relevant resource.
- Confirmation modal with operation name, blast radius, reversibility.
- Typed-confirmation input (the user types the exact target).
- Live status display while the operation runs.
- Result display with link to audit-log entry.

Per-page frontend tests (4-6 each): rendering states, confirmation enforcement, error states.

### Day 3.7: Terraform - IAM least-privilege per operation

```
infra/dev/iam/main.tf
```

New role `panakoes-dev-admin-api-task`. Policies are scoped per-operation:
- `drain` policy: `dynamodb:UpdateItem` on the runtime-config table only.
- `terminate-session` policy: `dynamodb:UpdateItem` on the session-state table + `ec2:TerminateInstances` on instances tagged `Service=streaming-transcriber`.
- `rotate-key` policy: `secretsmanager:UpdateSecret` on the JWT signing-key secret only + `dynamodb:Query` on the session table for counting active sessions.

The discipline is: each operation has the SMALLEST permission set that lets it succeed and nothing else. The drain command cannot terminate sessions; the rotate-key command cannot drain services. Verified by a Terraform unit test that asserts each operation's IAM policy doesn't grant unrelated permissions.

### Phase 3 deliverables

- admin-api service running with three operations.
- Three lifecycle dashboard pages.
- IAM least-privilege policies per operation.
- Audit trail for every Tier 3 invocation.
- Step-up MFA + typed confirmation safety pattern proven against three real operations.

### Phase 3 rollback

- Per-operation feature flags (`feature.lifecycle_drain_enabled`, etc.).
- IAM policies can be revoked without removing the role; the role keeps existing for audit purposes.
- Disable admin-api at the API Gateway level (route returns 503) if a coarser kill-switch is needed.

---

## Phase 4: Tier 3.2 - remaining lifecycle operations (1.5 weeks)

Goal: layer on the higher-blast-radius operations now that the safety pattern is proven.

### Operations (in increasing blast-radius order)

#### Day 4.1: Force-clean CloudFront cache
- Lower risk; just creates an invalidation.
- IAM: `cloudfront:CreateInvalidation` on the dev distribution only.
- Tests (4): happy path, target validation, cost-aware (an invalidation costs $0.005 per path past the free tier; emit a span attribute for the cost).

#### Day 4.2: Trigger immediate Dependabot scan
- Generates PRs but no production impact.
- Implementation: GitHub API call to fire the `dependabot/dependabot-core` schedule.
- Tests (3): happy path, rate-limit handling, audit on outcome.

#### Day 4.3: Pause / resume async transcription queue
- Mutates ECS service desired count for the transcriber-batch service.
- Reversibility: paired `pause` + `resume` commands.
- IAM: `ecs:UpdateService` scoped to `panakoes-dev-transcriber-batch` only.
- Tests (6 - paired-operation testing pattern): pause, resume, pause-then-resume cycle, double-pause idempotency, double-resume idempotency, queue depth reflects pause state.

#### Day 4.4: Promote a Whisper model version
- Mutates the GPU AMI parameter or a model-version DynamoDB pointer that the transcriber-batch container reads at task start.
- Reversibility: paired `promote` + `demote` commands.
- IAM: `ssm:PutParameter` on a single model-version parameter.
- Tests (5): promote, demote, target validation (only known model versions accepted), tag-write idempotency, audit captures both old and new model versions.

#### Day 4.5: Manually invoke a Step Functions state machine
- For the long-audio fan-out flow (`infra/dev/step-functions/`).
- Useful when a customer's job got stuck and operator needs to re-run.
- IAM: `states:StartExecution` on the long-audio state machine only.
- Tests (4): happy path, target validation, audit on outcome (executionArn captured), cost discipline (a failed Step Functions execution still costs the standard charge per state transition).

### Day 4.6-4.7: Frontend - five lifecycle pages

Same shape as Phase 3 frontend. Two pages have paired operations (transcription pause/resume, model promote/demote) so they're a single page with two buttons; three are standalone.

### Phase 4 deliverables

- Five additional operations exposed.
- Two paired-operation pages, three standalone pages.
- IAM policies per operation, audit-before-AND-after on every operation.
- Total of 8 lifecycle operations exposed (3 from Phase 3 + 5 from Phase 4).

### Phase 4 rollback

Same per-operation feature flag pattern. Each operation's flag is independent; if one operation is misbehaving, the others stay live.

---

## Phase 5: Tier 3.3 - audit log read view (0.5 week)

Goal: give the operator a UI to inspect what's been done.

### Day 5.1: Backend - audit query endpoint (TDD)

**Files:**

```
services/admin-api/src/panakoes_admin_api/
  routes/audit.py          # GET /api/v1/audit/events
  audit_query.py            # DynamoDB query against the audit-log GSI
services/admin-api/tests/
  integration/test_audit_endpoint.py
```

**Endpoint contract:**

```
GET /api/v1/audit/events
   ?actor_id=<id>
   &action=<lifecycle-action>
   &target=<resource>
   &from=<iso8601>
   &to=<iso8601>
   &limit=<int>
   &cursor=<opaque>

200 OK
{
  "events": [...],
  "next_cursor": "..."  // null if no more pages
}
```

**Tests (6):** filtering by each parameter, cursor pagination, non-admin returns 403, no-results case.

### Day 5.2: Frontend - audit log page

**Files:**
- `services/admin/src/routes/dashboard/lifecycle/audit/+page.svelte`
- Filters (actor, action, target, time range), table view, "show full event" expand.

**Tests (5):** filter wiring, pagination, empty state, loading state, error state.

### Phase 5 deliverables

- Operator can query who did what when via the dashboard.
- Filtering by all four key dimensions (actor, action, target, time).
- Pagination working with stable cursors.
- Last UI piece of Tier 3 in place.

### Phase 5 rollback

`feature.lifecycle_audit_enabled = false` removes the page. Audit data continues to flow into the table regardless.

---

## Cross-cutting concerns

### Observability

Every operation in cost-api and admin-api emits a span with:
- `panakoes.operation.name`: e.g., `cost.by_service`, `lifecycle.drain_streaming_service`.
- `panakoes.operation.tier`: 2 or 3.
- `panakoes.operation.tenant_id`: when the operation has a tenant scope.
- `panakoes.operation.outcome`: success | failure | rejected | cache-hit (for cost-api).
- `panakoes.operation.processing_time_ms`.
- For Tier 3: `panakoes.tier3.actor_id`, `panakoes.tier3.target`, `panakoes.tier3.audit_record_id`.

Plus counter `panakoes.operations_total{operation_name, tier, outcome}` and histogram `panakoes.operation_duration_ms{operation_name}`.

### Alerts (CloudWatch)

- cost-api 5xx rate > 1% over 5 min → SNS to system-alerts.
- cost-api p99 latency > 2s over 5 min → SNS.
- admin-api 5xx rate > 1% over 5 min → page (this one is more sensitive because it's the lifecycle path).
- admin-api 401/403 spike > 100/min → SNS (potential brute-force on lifecycle endpoints).
- AWS Budgets 80% threshold (existing alert path; surfaces in dashboard via Phase 2.3).

### Runbooks

Three new runbook entries land alongside the relevant phases:

- `docs/runbooks/cost-explorer-rate-limit.md` (Phase 1) - what to do when CE returns ThrottlingException repeatedly.
- `docs/runbooks/lifecycle-operation-failure.md` (Phase 3) - what to do when a lifecycle operation fails mid-flight; how to use the audit log to determine current state.
- `docs/runbooks/audit-log-investigation.md` (Phase 5) - how to investigate "who did this when" using the audit query endpoint.

### ADRs

Two new ADRs land:

- ADR-031: cost data caching strategy (1-hour TTL on daily-granularity, 24-hour on monthly; CE query budget management).
- ADR-032: typed-confirmation string + step-up MFA + audit-before-AND-after as the universal Tier 3 safety pattern.

### CHANGELOG

Each phase's PRs add CHANGELOG entries under `[Unreleased]` in the appropriate category. Phases 1-2 are Added; Phase 3 has both Added (the operations) and Security (the audit/MFA discipline). Phases 4-5 are Added.

### Test coverage gates

- cost-api: 80% line / 80% branch.
- admin-api: 100% line / 100% branch (project rule for audit/billing-adjacent code).
- Frontend cost pages: 80%.
- Frontend lifecycle pages: 100% (mirrors backend; the user-facing confirmation flows are part of the audit/security path).

CI fails the PR below thresholds.

---

## Operational handoff

By end of Phase 5, the maintainer should:

- Run each Tier 3 operation against the dev environment once, end-to-end. Verify the audit log entry, verify the runtime effect (service drained, session terminated, etc.), verify the rollback (un-drain, key un-rotation, etc. where reversible).
- Confirm the three new runbook entries are linked from `docs/runbooks/README.md`.
- Confirm the two new ADRs are listed in any ADR index doc.
- Confirm CloudWatch alarms fire correctly by manually triggering the failure conditions in dev (e.g., set a budget below current spend to fire the Budgets alarm).
- Smoke the cost-api endpoints from the deployed dashboard against real (not mocked) AWS data.

The handoff is complete when the maintainer can answer "who, what, when, why, and what's the rollback" for any Tier 3 operation without consulting docs.

---

## Risk register

Risks tracked across the entire plan, sorted by impact-times-likelihood.

### High impact, medium likelihood

**R1: Cost Explorer query budget overrun.** CE costs $0.01 per query past the free tier (1000 queries/month). The dashboard's reload pattern + cache-bypass-in-debug + ad-hoc operator queries can plausibly add up to thousands of queries/month if cache is mis-configured.

**Mitigation:** cache TTL is mandatory. cost-api refuses to call CE if the cache is unreachable (DynamoDB outage in cost-cache table → return stale data with a flag, never query CE). Dashboard never bypasses the cache. Monthly CE-call counter surfaced in Phase 2's anomaly page so we see the cost-of-cost-data trend.

**R2: Step-up MFA UX rejection.** Re-prompting MFA every 5 minutes for high-blast-radius operations is intentional but operators will hate it. If the operator response is "I'll just keep the MFA window fresh by hitting refresh constantly," the friction is bypassed in practice.

**Mitigation:** the same step-up window grants a single subsequent operation against the same target (DRAIN auth, then UNDRAIN auth within 5 minutes uses the same step-up). Different targets re-prompt. Quantify the friction by surfacing "your last step-up was X seconds ago" in the modal so operators trust the budget.

**R3: Audit-log write failure mid-operation.** If the audit record fails to write while the operation succeeds, we have a state-of-the-world we can't reconstruct.

**Mitigation:** audit writes are issued asynchronously to a dedicated SQS queue with a fallback to CloudWatch Logs. The operation is allowed to proceed even if the audit write is in flight; the SQS-to-DynamoDB consumer retries until success or DLQ. A CloudWatch alarm fires if the audit-DLQ has any messages.

### Medium impact, medium likelihood

**R4: Tagging discipline gaps.** Phase 2's per-tenant cost rollup depends on per-resource `Tenant=<id>` tags. If a Terraform module is missed, that resource's cost bleeds into the amortized fixed-costs bucket and tenant attribution drifts.

**Mitigation:** add a `tflint` rule that fails CI when a panakoes-namespaced resource lacks the `Tenant` tag (with an explicit allow-list of resources that can't be tenant-scoped, like NAT and KMS). Phase 0 includes this rule.

**R5: Confirmation-string fatigue.** Operators will copy-paste the confirmation string from the modal. The friction is in reading the modal text first; if the modal text is misleading, the operator might confirm the wrong action.

**Mitigation:** the confirmation string is always exactly the target visible in the modal, generated server-side. If it doesn't match, the action is rejected. Modal text is reviewed by the maintainer for clarity in each phase's frontend test pass.

### Low impact, high likelihood

**R6: Cost-api forecast inaccuracy.** AWS CE Forecast is unreliable for short months and during traffic ramp-ups. Operators will complain that the projection is wrong.

**Mitigation:** display BOTH the CE forecast AND our linear-extrapolation; flag when they diverge by more than 25%. Document forecast accuracy expectations in the runbook (within 10% for days 1-14 of a month, within 20% for days 15-30).

**R7: SvelteKit routing edge cases on dynamic params.** SvelteKit 2.59's stricter typing on `$page.params.X` produces `string | undefined`. The Tier 1 dashboard already had to cast; Tier 3's dynamic-target pages will hit the same.

**Mitigation:** centralize the cast pattern in a `$lib/utils/route-param.ts` helper; apply consistently across Tier 3 pages.

---

## Cost projection

A rough monthly run-rate forecast for the deployed system after Phase 5 ships, against the dev account.

| Resource | Monthly cost (estimated, USD) | Notes |
|---|---|---|
| cost-api ECS Fargate task | ~$15 | 0.25 vCPU / 0.5 GB; runs 24/7. |
| admin-api ECS Fargate task | ~$15 | Same shape. |
| 4 new DynamoDB tables (PAY_PER_REQUEST) | ~$2 | Low write volume; cost dominated by storage at $0.25/GB-month. |
| AWS Cost Explorer queries (cached, ~50/day average) | ~$15 | Past the 1000/month free tier. |
| AWS Budgets (2 monthly budgets) | $0 | Free for the first 62 budgets/month. |
| CloudWatch alarms (8 new) | ~$0.80 | $0.10/alarm/month. |
| SNS notifications | <$1 | Pennies at our volume. |
| Bandwidth between admin-api and DynamoDB | <$1 | All in-VPC. |
| **Estimated total** | **~$50/month** | |

The projection is sensitive to the CE query rate. If cache is mis-configured and we hit CE 1000+ times beyond the free tier, costs balloon. The R1 mitigation (refuse to call CE on cache-table outage) is the safety belt.

---

## Sequencing summary + Gantt

```
Week 1  | [Phase 0: Foundation         ]
Week 2  |                              [Phase 1: Tier 2.1: by-service        ]
Week 3  |                                                                   [Phase 2: Tier 2.2: tenant + forecast + anomalies ]
Week 4  |                                                                                                                    [Phase 3.1-3.4: admin-api + 3 ops    ]
Week 5  |                                                                                                                                                       [Phase 3.5-3.7: frontend + IAM     ]
Week 6  |                                                                                                                                                                                          [Phase 4.1-4.5: 5 more ops + frontend  ]
Week 7  |                                                                                                                                                                                                                                  [Phase 5: audit log view  ]

(Real calendar is more like 12 weeks for solo + interleaved priorities.)
```

**Critical path:**

1. Phase 0 day 1 (Terraform DynamoDB tables) gates everything; nothing else can start until those tables exist.
2. Phase 1 day 1-2 (cost_explorer + cache) gates Phase 1 day 3 (the first endpoint).
3. Phase 3 day 1 (step-up gate + audit wrapper) gates Phase 3 day 2-4 (the three operations).
4. Phase 5 depends on phases 3 + 4 having produced audit data.

**Parallelizable:**

- Phase 0 day 2 (service templates) and day 3 (frontend scaffolding) can run in parallel.
- Phase 2 days 1, 2, 3 (per-tenant, forecast, anomalies) can run in parallel.
- Phase 4 days 1-5 (five operations) can run in parallel because each has its own IAM scope.

---

## Definition of done (overall)

Tier 2 + Tier 3 are done when:

- Every page from the design doc renders against real AWS data.
- Every operation from the design doc has been exercised end-to-end in dev with an audit-log entry produced.
- Every IAM policy has been verified to grant exactly the operations its corresponding command needs and no more (the "cannot drain when terminating sessions" assertion).
- Every Tier 3 operation has a paired feature flag so the maintainer can disable any single operation without redeploying.
- Three new runbook entries are linked from `docs/runbooks/README.md` and have been smoke-tested by the maintainer running each one once.
- Two new ADRs (031 + 032) are in the ADR index.
- CloudWatch alarms for both services are firing correctly; verified by manual trigger of each alarm condition.
- Test coverage at the project gates (80% cost-api, 100% admin-api).
- The dashboard's `/dashboard/cost` and `/dashboard/lifecycle` are linked from the main navigation and are visually consistent with the existing Tier 1 health pages.

When all of the above are true, mark tasks #24 (Admin Dashboard Tier 2) and #25 (Admin Dashboard Tier 3) as complete in the task list and update CHANGELOG.md `[Unreleased]` with the v0.2.0 milestone summary.

---

## References

- [`admin-dashboard-tier-2-3.md`](./admin-dashboard-tier-2-3.md): the design doc this plan implements.
- [`transcriber-abstraction.md`](./transcriber-abstraction.md): companion design (cost-api consumes the per-backend cost estimator from this).
- [`docs/architecture.md`](../architecture.md): the system this layers on top of.
- [`docs/operations/ci.md`](../operations/ci.md): the CI/CD discipline every PR in this plan respects.
- [`SCOPE.md`](../../SCOPE.md): MVP scope locks Tier 1 (shipped) and names Tiers 2-3 as v0.2 commitments.
- ADR-022 (JWT auth), ADR-023 (audit library), ADR-024 (orchestrator-delegation pattern), ADR-026 (CHANGELOG merge=union), ADR-027 (concurrency), ADR-028 (PAT cascade), ADR-029 (Dependabot grouping), ADR-030 (bypass actor).
