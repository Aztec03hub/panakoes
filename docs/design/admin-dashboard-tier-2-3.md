# Admin Dashboard Tier 2 (cost) + Tier 3 (lifecycle) design doc

> **Status:** Proposed. Design doc, not implementation. The admin dashboard's Tier 1 (read-only health) shipped in PR #56; this doc plans Tiers 2 and 3.

## Goal

Extend the SvelteKit admin dashboard from "read-only service health" (Tier 1, shipped) to:

- **Tier 2: cost and budget tracker.** Surface AWS cost broken down by service / environment / tag, project monthly burn against a configured budget, alert on burn-rate anomalies. Read-only.
- **Tier 3: secure lifecycle controls.** Allow an authenticated admin (with step-up MFA) to perform high-impact lifecycle operations: drain a service, terminate a stuck GPU session, force-rotate the JWT signing key, manually trigger a dependency update, etc. Mutating; high-blast-radius.

Tier 4 (deep observability dashboards, log search, distributed-tracing UI) is explicitly deferred to phase 2 per `SCOPE.md`.

## Tier 2: cost and budget tracker

### Data sources

AWS Cost Explorer (CE) is the canonical cost data source. CE provides:

- Daily-granularity usage and cost broken down by `LINKED_ACCOUNT`, `SERVICE`, `USAGE_TYPE`, and arbitrary user tags.
- Up to 12 months of history.
- Forecast API that projects monthly spend from current burn rate.

Secondary sources for finer-grained breakdowns:

- AWS Budgets API for the configured budget thresholds and alert state.
- CloudWatch Metrics for real-time per-service usage (e.g. NAT egress bytes per hour, GPU instance-hours).
- DynamoDB cost tracking table (panakoes-managed) for event-driven costs that don't surface in CE clearly: per-tenant transcription minutes, per-tenant Anthropic API spend.

### Backend

A new `services/cost-api` Python service that:

1. **Caches CE responses.** CE costs $0.01 per query past the free tier. Cache CE responses in DynamoDB with a TTL of 1 hour for daily-granularity data. The cache key includes the date range and group-by dimension; a request for "this month broken down by service" hits CE once per hour and serves all subsequent dashboard requests from cache.
2. **Tags every panakoes resource.** Every Terraform-provisioned resource carries `Project=panakoes`, `Environment=<env>`, `Service=<service>` tags. The `default_tags` block in each `infra/dev/<X>/providers.tf` ensures this; a missing tag fails CI via `tflint` + a custom rule.
3. **Computes derived metrics.** Per-tenant cost = (transcription minutes * per-minute cost of selected backend) + (Claude API spend * margin). Surfaced as a tenant-level breakdown for the eventual customer-facing usage page.
4. **Exposes a stable JSON contract** consumed by the SvelteKit dashboard. Versioned (`/api/v1/cost/...`) so the frontend can evolve independently.

### Frontend (SvelteKit dashboard, Tier 2 routes)

```
/dashboard/cost                 (Tier 2 home)
/dashboard/cost/by-service       (breakdown by AWS service)
/dashboard/cost/by-tenant        (breakdown by tenant)
/dashboard/cost/forecast         (this-month projection vs budget)
/dashboard/cost/anomalies        (recent burn-rate alerts)
```

Charting via [Recharts](https://recharts.org/) or [Layer Chart](https://layerchart.com/) (Svelte-native, smaller bundle). Stick with [`bits-ui`](https://bits-ui.com/) for primitive components (consistent with Tier 1).

Every page is read-only; no mutations from Tier 2.

### Budget alerts

AWS Budgets alerts route through SNS to the `panakoes-dev-system-alerts` topic (provisioned in `infra/dev/events/`). The `notification` service consumes that topic and persists alerts to DynamoDB; the dashboard's `/dashboard/cost/anomalies` page reads from that table.

Alert thresholds (initial, all configurable via Terraform variables):

- 50% of monthly budget consumed → informational.
- 80% of monthly budget consumed → warning.
- 100% of monthly budget projected (forecast API) → critical.
- Any single service crossing 30% of monthly budget → investigate.

### Acceptance criteria

- Cost-by-service table loads in under 500ms from cache, under 5s on cache miss.
- Forecast accuracy: within 10% of actuals for the first 14 days of a month, within 20% for days 15-30. (Forecast quality degrades for short months; AWS CE itself is the bottleneck.)
- Per-tenant attribution covers 100% of variable costs (transcription, summarization). Fixed costs (NAT, monitoring overhead) are amortized across all tenants pro-rata to active-minute usage.
- Zero direct mutations possible from Tier 2 routes (verified by integration test that asserts only `GET` requests pass the auth middleware on these routes).

## Tier 3: secure lifecycle controls

### Operations exposed

| Operation | Blast radius | Reversibility |
|---|---|---|
| Drain a streaming service (refuse new sessions, allow existing to drain) | Active sessions on that service | Reversible by re-enabling |
| Terminate a stuck GPU session | One tenant's session | Irreversible (session is over) |
| Force-rotate JWT signing key | All active sessions invalidated | Irreversible (forces re-login) |
| Trigger an immediate Dependabot scan | Generates PRs (cosmetic) | Reversible |
| Pause / resume async transcription queue | All in-flight transcriptions delayed | Reversible |
| Promote a model version (Whisper-large-v3 -> v4 staging -> v4 prod) | Future transcription accuracy | Reversible by demoting back |
| Manually invoke a Step Functions state machine (long-audio fan-out) | One tenant's job | Irreversible if step machine has side effects |
| Force-clean a CloudFront cache | Frontend perceived state | Reversible (cache will refill) |

Excluded from Tier 3 (require AWS Console access or a dedicated runbook because their blast radius warrants out-of-band approval):

- Dropping a database table or running data-mutating SQL.
- Modifying IAM roles or policies.
- Deleting infrastructure (S3 buckets, KMS keys, etc.).
- Changing Stripe pricing or refunding charges.

### Authentication and authorization

Every Tier 3 mutation requires:

1. **Active session** with admin role (existing Better-Auth RBAC).
2. **Step-up MFA re-authentication** within the last 5 minutes (existing Better-Auth step-up support).
3. **Confirmation modal** with the operation name, blast radius, and reversibility shown explicitly.
4. **Typed confirmation string** (e.g., type "DRAIN streaming/auth-prod" to confirm). The exact string is operation-and-target-specific so muscle memory can't confuse two operations.

### Audit trail

Every Tier 3 invocation writes a record to the audit DynamoDB table (provisioned in `infra/dev/data/`) with:

- `actor_id` (the admin's user id)
- `action` (the operation name)
- `target` (the specific resource)
- `timestamp` (UTC, ISO 8601)
- `source_ip` (from the request)
- `parameters` (the full request body, serialized)
- `outcome` (success, failure, error message)

The audit log is append-only, KMS-encrypted, and replicated to S3 archive. Read access via the eventual `/dashboard/audit` page (Tier 3+).

### Backend

A new `services/admin-api` Python service that:

1. **Holds the lifecycle command catalog.** Each command is a typed Pydantic model with the parameters it requires, the IAM permissions it needs, the audit-log shape, and the idempotency semantics.
2. **Verifies step-up MFA on every request.** The `panakoes-auth-client` library checks for a `mfa_step_up_at` claim in the JWT and rejects requests where it's older than 5 minutes.
3. **Issues the underlying AWS API calls** with the appropriate task role (least-privilege, per-operation; "drain a service" cannot also "terminate a GPU session").
4. **Writes the audit record before AND after the operation.** Before (intent) so a failed operation still shows the attempt; after (outcome) so we have ground truth.

### Frontend (SvelteKit dashboard, Tier 3 routes)

```
/dashboard/lifecycle                  (Tier 3 home, command list)
/dashboard/lifecycle/drain            (drain-service flow)
/dashboard/lifecycle/sessions         (active streaming sessions, terminate buttons)
/dashboard/lifecycle/keys             (JWT key rotation)
/dashboard/lifecycle/transcription    (queue pause/resume, model promotion)
/dashboard/lifecycle/audit            (audit log read view; Tier 3 boundary)
```

Each lifecycle page enforces the step-up-MFA / confirmation-string flow client-side as the first line of defense; the backend enforces the same as the actual gate. Defense in depth: even if a client-side bypass were possible, the server-side check holds.

### Acceptance criteria

- Every Tier 3 mutation requires both step-up MFA AND a confirmation string. Verified by integration test that asserts a request without either header is rejected with 401/403.
- Every Tier 3 mutation produces an audit record before AND after, even on failure. Verified by integration test that simulates a failure mid-operation and asserts both records exist.
- The blast-radius display in the confirmation modal is text-matched to the audit record's `target` field. Operators see exactly what they're confirming.
- The audit log is queryable by `actor_id` (who did what), `action` (what got done), `target` (what was the subject), and `timestamp` range. Verified by acceptance-test queries against a seeded test fixture.

## Phasing

**Tier 2 phase 1 (~1 week of focused work):** services/cost-api skeleton + cache layer + DASHBOARD by-service breakdown page. Validates the data plumbing.

**Tier 2 phase 2 (~1 week):** by-tenant breakdown + forecast page + anomalies page. Requires the panakoes audit log and DynamoDB cost-tracking table to be populated; demands tagging discipline across all infra modules.

**Tier 3 phase 1 (~1.5 weeks):** services/admin-api skeleton + step-up-MFA gate + audit logging + first three operations (drain, terminate session, key rotation). Lower-blast-radius operations first; validate the safety pattern.

**Tier 3 phase 2 (~1.5 weeks):** remaining operations (queue pause/resume, model promotion, cache invalidation, Step Functions invocation). Higher-blast-radius operations after the safety pattern is proven.

**Tier 3 phase 3 (~0.5 week):** the audit log read view (`/dashboard/lifecycle/audit`).

Total estimate: about 6 weeks of focused work for Tiers 2 + 3 end-to-end. Realistic with normal solo cadence and other commitments interleaved.

## Risks

- **Cost Explorer query budget.** CE charges for queries past the free tier. The dashboard's reload pattern could blow past the limit if cache-bypass is ever wired. Mitigation: cache TTL is mandatory; cache-miss circuit-breaker if CE returns rate-limit errors.
- **Step-up MFA UX friction.** Re-prompting for MFA every 5 minutes is intentional for blast-radius reasons but operators will hate it. Mitigation: short-circuit the prompt when the action is the SAME action as the one whose step-up is still valid (re-using the existing 5-minute window for the same operation). New operations re-prompt.
- **Audit log write failures.** A failed audit-log write must not block the operation from running (or vice versa). Mitigation: audit log writes are issued asynchronously to a dedicated SQS queue; if the queue is down, the operation logs to CloudWatch Logs as a fallback and an oncall page fires.
- **Confirmation-string fatigue.** Operators will copy-paste the confirmation string from the modal into the input. Mitigation: the confirmation string is ALWAYS the action+target, so a copy-paste IS the correct action; the friction is in reading the modal text first. Acceptable.

## References

- [`SCOPE.md`](../../SCOPE.md): MVP scope locks Tier 1 in v0.1, Tiers 2-3 explicitly named, Tier 4 deferred.
- [`docs/architecture.md`](../architecture.md): the broader system the admin dashboard surfaces.
- ADR-022 (JWT auth), ADR-023 (audit library), ADR-024 (orchestrator-delegation pattern).
- AWS Cost Explorer documentation: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_Operations_AWS_Cost_Explorer_Service.html
- AWS Budgets documentation: https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html
- Better-Auth step-up MFA: see `services/auth/` docs.
