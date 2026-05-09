# dev/admin-state

Per-environment DynamoDB tables for the dev environment that back the
admin dashboard's Tier 2 (cost and budget tracker) and Tier 3 (secure
lifecycle controls) features.

This module is the Phase 0 foundation called out in
`docs/design/tier-2-3-implementation-plan.md`. It exists alongside
`dev/data/` rather than inside it because Tier 3 is the most dangerous
code path in the system; isolating its backing state to a separate
Terraform configuration gives those changes their own apply boundary
and prevents an unrelated change to ingestion / audit / streaming-sessions
state from accidentally damaging the cost or lifecycle tables (and
vice versa). Cost-api iteration speed also benefits: Phase 1 and
Phase 2 will touch this module repeatedly without re-applying the
production-critical `dev/data/` state.

## Tables

| Name                            | Hash key             | Range key | TTL attribute  | GSIs | Purpose                                                                                       |
|---------------------------------|----------------------|-----------|----------------|------|-----------------------------------------------------------------------------------------------|
| panakoes-dev-cost-cache         | cache_key            | -         | expires_at     | -    | Cache of AWS Cost Explorer query results so dashboard pages render fast and avoid CE fees.    |
| panakoes-dev-tenant-cost-rollup | tenant_id            | day       | -              | -    | Per-tenant per-day pre-aggregated cost numbers for the Tier 2.2 tenant view.                  |
| panakoes-dev-lifecycle-state    | idempotency_key      | -         | expires_at     | -    | Tier 3 lifecycle operation envelope. Powers the idempotent-by-key safety pattern.             |
| panakoes-dev-alert-state        | alert_signature      | -         | expires_at     | -    | Anomaly detector dedup state so the same signature does not refire across polling intervals. |

All tables share the same defaults:

- `billing_mode = PAY_PER_REQUEST`
- AWS-managed server-side encryption (matches `dev/data/`; see the
  encryption note in `main.tf` for the trade-off rationale)
- Point-in-time recovery enabled
- Deletion protection enabled
- Standard `Project / Environment / ManagedBy / Module` tags

## Apply

```bash
cd infra/dev/admin-state
terraform init
terraform plan
terraform apply
```

Phase 0 only creates the tables. The cost-api and admin-api services
that read and write them are introduced in Phase 1 and Phase 3 of the
implementation plan respectively.

## Related

- `infra/dev/data/` holds the production-critical tables (ingestion,
  audit-log, streaming-sessions). The audit-log table also gains a
  `Tier3ActionIndex` GSI in the same PR that introduces this module
  (Phase 0 prep for the Tier 3.3 audit log read view).
- `docs/design/admin-dashboard-tier-2-3.md` describes the dashboard
  surface these tables back.
- `docs/design/tier-2-3-implementation-plan.md` is the phased plan
  this module sits at the foundation of.
