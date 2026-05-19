# Scripts

Operator scripts for Panakoes. Each script documents its own purpose,
preconditions, and rollback path.

## `seed-cost-api-dynamodb.py`

One-off seed for the four DynamoDB tables backing the cost-api admin
dashboard pages (`/cost/by-service`, `/cost/by-tenant`, `/cost/forecast`,
`/cost/anomalies`). The script writes synthetic but realistic data so
those pages render rich content during the E2E sign-in smoke test, before
the nightly cost-rollup-aggregator and the AWS Cost Anomaly Monitor have
produced any real signal.

### When to run

- One-time after a fresh `infra/dev/admin-state` apply when the tables
  exist but are empty.
- Whenever you want a clean demo dataset for screenshots, recordings, or
  a sales conversation.
- Re-run after a destructive cleanup (see Cleanup below).

The script is **idempotent**: it overwrites prior items by PK so it is
safe to re-run. Re-running with the same UTC date produces byte-identical
output because the synthetic generator uses a fixed random seed.

### How to run

```bash
# From the repo root, with cost-api's uv env so boto3 + pydantic are present:
cd services/cost-api
uv run python ../../scripts/seed-cost-api-dynamodb.py --dry-run    # preview
uv run python ../../scripts/seed-cost-api-dynamodb.py              # apply

# Helpful flags:
#   --limit 3        cap each table's write count (smoke testing)
#   --days 14        change the historical window (default 30)
#   --force          bypass the dev-account guard (DO NOT USE in prod)
```

Environment:

- `AWS_PROFILE` defaults to whatever your shell exports; `panakoes-admin`
  is the standard local profile.
- `AWS_REGION` defaults to `us-east-1`.
- The script refuses to run unless `sts:GetCallerIdentity` returns the
  dev account (`659225405128`). Use `--force` to override **only** if you
  know exactly why.

### What gets written

| Table | Rows | Shape |
|---|---|---|
| `panakoes-dev-cost-cache` | 6 | Two per window (by-service + by-tenant) for the 7 / 14 / 30 day windows ending today UTC. Each row is a fully-rendered `CostBreakdown` or `TenantCostBreakdown` JSON envelope. TTL = 7 days. |
| `panakoes-dev-tenant-cost-rollup` | 150 | One row per (tenant_id, day) across 5 synthetic tenants for the last 30 days. |
| `panakoes-dev-alert-state` | 2 | One Bedrock cost-spike anomaly (~3x expected) and one DynamoDB rate-of-change anomaly (~2.5x expected). TTL = 24 hours. |
| `panakoes-dev-audit-log` | skipped | Owned by admin-api; not consumed by any cost dashboard page. |

### Cleanup

To clear the seed data from a single table (run per-table as needed):

```bash
TABLE=panakoes-dev-cost-cache
PK=cache_key                         # cache_key for cost-cache
                                     # alert_signature for alert-state
                                     # tenant_id + day for tenant-cost-rollup
aws dynamodb scan \
  --table-name "$TABLE" \
  --projection-expression "$PK" \
  --query "Items[*].${PK}.S" \
  --output text | tr '\t' '\n' \
  | while read -r key; do
      aws dynamodb delete-item \
        --table-name "$TABLE" \
        --key "{\"$PK\": {\"S\": \"$key\"}}"
    done
```

For the tenant-cost-rollup table (composite key), include `day` in the
key payload:

```bash
aws dynamodb scan --table-name panakoes-dev-tenant-cost-rollup \
  --projection-expression "tenant_id,#d" \
  --expression-attribute-names '{"#d":"day"}' \
  --query 'Items[*].[tenant_id.S,day.S]' --output text \
  | while read -r tid day; do
      aws dynamodb delete-item --table-name panakoes-dev-tenant-cost-rollup \
        --key "{\"tenant_id\":{\"S\":\"$tid\"},\"day\":{\"S\":\"$day\"}}"
    done
```

Once the real cost-rollup-aggregator runs nightly and the AWS Cost
Anomaly Monitor is configured, the seeded rows will mix with real data.
The TTL on `cost-cache` and `alert-state` cleans up automatically; the
`tenant-cost-rollup` table has no TTL so you must manually purge the
seed rows if you do not want them in your historical view.

### Tests

Shape and reconciliation tests for the synthetic data live at
`tests/scripts/test_seed_cost_api.py` and run under the cost-api uv env:

```bash
cd services/cost-api
uv run pytest ../../tests/scripts/ --no-cov
```

The tests guard against drift between the synthetic generator and the
`CostBreakdown` / `TenantCostBreakdown` / `CostAnomaly` pydantic models
that the cost-api route layer consumes.

## Other scripts

Each of these is documented in its own header comment. Run with `-h` or
read the file for usage:

- `branch-prune.sh [--dry-run]`: delete local branches whose remote PR is
  MERGED or CLOSED. Squash-merged PRs leave dangling local branches that
  `git branch --merged` misses; this script asks GitHub directly. Pruned
  48 stale branches in one pass on 2026-05-19. Run periodically (weekly
  or after a heavy session).
- `ci-pr.sh`: scoped CI mirror that runs only the gates relevant to the
  files changed against `origin/main`.
- `design-review.sh <stage> <design-doc-path>`: mechanical setup for the
  design-review cycle (WORKFLOW.md 5.6). Creates the worktree and fills
  the right brief template (architect or adversarial) into
  `<worktree>/AGENT_BRIEF.md`. Orchestrator then dispatches the agent
  with that brief. Use `architect` for Stage 1, `adversarial` for Stage 3.
- `ci-local.sh` (via `make ci-local`): full local CI sweep.
- `check_no_em_dashes.sh`: pre-commit hook enforcing Phil's no-em-dash
  rule.
- `dev-up.sh`: spin up the docker-compose dev stack.
- `gpr-fix-merge.sh`: rebase + force-push + auto-merge helper.
- `install-githooks.sh`: configure this clone to use `.githooks/`.
- `pr-status.sh`: one-line digest of every open PR's queue state.
- `required-checks-add.sh`: add a required CI check to the branch
  protection ruleset.
- `run-auth-migration.sh`: invoke the auth service's Drizzle migration
  runner via `aws ecs run-task`.
- `tf.sh`: thin Terraform wrapper that injects `AWS_PROFILE` and the
  dev S3 backend config.
- `wait-for-pr.sh`: poll one or more PRs until their queue state matches
  a predicate.
