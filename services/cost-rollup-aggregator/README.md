# services/cost-rollup-aggregator

AWS Lambda function that runs nightly to populate `panakoes-dev-tenant-cost-rollup` from AWS Cost Explorer per-tenant data. Without this populator the cost-api `GET /api/v1/cost/by-tenant` route returns empty rows even when CE has spend data; this Lambda is the missing producer.

## Trigger

EventBridge Scheduler (`infra/dev/cost-rollup-aggregator/`) fires the function once per day at 02:00 UTC. 02:00 UTC is the lowest-traffic window for the AWS billing API and gives Cost Explorer 2 hours past the UTC-day boundary to settle refreshed numbers before we read them.

The handler also accepts an explicit `event["day"]` ISO date string for manual replays of a specific historical day.

## What it does

For the target day (`yesterday-UTC` by default) the aggregator:

1. Calls `ce.get_cost_and_usage` with `Granularity=DAILY`, `Metrics=["UnblendedCost"]`, and the two-dimensional `GroupBy=[{Type: TAG, Key: tenant_id}, {Type: DIMENSION, Key: SERVICE}]` over the window `[day, day + 1d)` (start-inclusive, end-exclusive). The two-dimensional GroupBy is post-ADR-040; CE returns one group per distinct `(tenant_id, service)` pair.
2. Follows `NextPageToken` for the multi-page case.
3. For each group, parses the two-element `Keys` (`["tenant_id$<value>", "<service>"]`), converts the USD `Amount` to integer cents via `Decimal` + `ROUND_HALF_UP`, and calls `TenantRollupStore.put_rollup(tenant_id, day, service, cost_cents)` to persist one row keyed `(tenant_id HK, "<day>#<service>" RK)`.
4. Writes any untagged spend (CE encodes the tag slot as `"tenant_id$"` with no trailing value) under the synthetic tenant id `__untagged__`, with the corresponding service preserved. The cost-api by-tenant route surfaces this row's per-service breakdown, which is operationally useful (operator sees both the magnitude AND composition of unattributed spend before per-tenant tagging is rolled out).
5. Returns a structured summary (`{day, tenants_written, rows_written, untagged_cost_cents, ce_calls, duration_ms}`) so CloudWatch Logs Insights can query daily aggregator runs. `tenants_written` counts distinct tenants (legacy shape); `rows_written` counts per-`(tenant, service)` rows persisted (post-ADR-040).

## Idempotency

DynamoDB `put_item` is an upsert, so re-running the aggregator for the same day overwrites the existing row. This is intentional: late-arriving CE refreshes plus retries should converge to the latest CE answer rather than double-count.

## Reuses cost-api primitives

The aggregator imports `panakoes_cost_api.tenant_rollup.TenantRollupStore` rather than re-implementing the DynamoDB write logic. The cost-api package is declared as an editable path-dep in `pyproject.toml`; the Dockerfile copies `services/cost-api/` into the build context and installs it explicitly so the resolved version is visible to the image scanner.

## Environment variables

| Variable | Required / Default | Description |
|---|---|---|
| `TENANT_COST_ROLLUP_TABLE` | required | DynamoDB table this Lambda writes to. Terraform pins this to `panakoes-dev-tenant-cost-rollup`. |
| `AWS_REGION` | auto (Lambda runtime) | Region for the boto3 clients. Defaults to `us-east-1` in tests. |

## Local development

Lambdas do not run a local server. Test with:

```bash
uv sync --group dev
uv run pytest
uv run ruff check
uv run mypy src
```

Tests use moto for DynamoDB and a hand-rolled `FakeCEClient` for Cost Explorer (moto's CE coverage is too shallow for the `get_cost_and_usage` shape we depend on).

## Deployment

Canonical bake path is GitHub Actions (`.github/workflows/image-bake-on-change.yml` on push to `main`, or the `image-bake-manual.yml` one-button workflow); the workflow handles multi-arch build, OIDC auth, and the ECR push. The local command below is a fallback for offline dev only.

The build context is the repo root because we COPY the sibling `services/cost-api/` path-dep into the image:

```bash
cd /path/to/panakoes
docker build \
    -f services/cost-rollup-aggregator/Dockerfile \
    -t panakoes-cost-rollup-aggregator .
```

The image follows the AWS Lambda container-image convention (`public.ecr.aws/lambda/python:3.12` base). The GHA workflow pushes to `panakoes-dev-cost-rollup-aggregator` ECR; Terraform updates the function's `image_uri` on the next apply.

## IAM dependencies (Terraform-managed)

The function role grants:

- `ce:GetCostAndUsage`, `ce:GetDimensionValues` on `*` (Cost Explorer has no resource-level authorization).
- `dynamodb:PutItem` on the `panakoes-dev-tenant-cost-rollup` table ARN only.
- CloudWatch Logs write to `/aws/lambda/panakoes-dev-cost-rollup-aggregator` (the standard Lambda log group).

## Operator follow-up

Per-tenant tagging on the actual AWS resources (`Project`, `Environment`, `tenant_id` on every billable resource) is a separate piece of work. Until that lands, every dollar of dev-environment spend lands in the `__untagged__` bucket. The aggregator still runs and the rollup table still populates; the by-tenant breakdown just shows one row labeled `__untagged__` with the full daily cost. Once the tagging policy is in place the same aggregator decomposes spend by tenant without any code change.
