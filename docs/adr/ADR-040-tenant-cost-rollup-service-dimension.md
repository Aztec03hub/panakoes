# ADR-040: Service dimension for the tenant cost rollup table

## Status

Accepted (2026-05-11).

## Context

The `panakoes-dev-tenant-cost-rollup` DynamoDB table (provisioned by
`infra/dev/admin-state/main.tf`) was originally keyed
`(tenant_id HK, day RK)` with a single `cost_cents` attribute. The
nightly aggregator (`services/cost-rollup-aggregator/`) populated it
by calling Cost Explorer with a one-dimensional `GroupBy = [TAG
tenant_id]`, and the cost-api `GET /api/v1/cost/by-tenant` route
returned a list of tenants with a single `cost_cents` total each.

The admin SPA's `/cost/by-tenant` page already renders a per-tenant
table; the design intent (documented in
`docs/design/admin-dashboard-tier-2-3.md`) is that each row expands to
show a per-service breakdown of that tenant's spend so an operator can
see "tenant X cost $400 this month, of which $250 was DynamoDB and
$150 was EC2." The current schema cannot persist that breakdown: with
only `(tenant_id, day)` as the key, every (tenant, service) pair on
the same day would collide and overwrite each other in DynamoDB.

PR #228's cost-seed-agent flagged the gap when it tried to author seed
fixtures that included a per-service dimension and discovered the
table had no place to put the data.

The table is empty in dev (no aggregator run has yet produced data),
which makes this a clean redesign window rather than an actual data
migration.

## Decision

### Sort key becomes composite: `day_service`

The table's sort key changes from `day` (ISO `YYYY-MM-DD`) to
`day_service` (composite `YYYY-MM-DD#<service>`). The hash key
(`tenant_id`) does not change. Two attributes are denormalized on
every row (`day` and `service` as separate `S` attributes) so readers
do not need to re-split the composite key on every read. The split is
owned by `tenant_rollup.py:_row_from_item`; writers always go through
`put_rollup`, which composes the key via `make_day_service_key`, so
the two halves cannot drift.

Concrete row example:

```
{
  "tenant_id":   "tenant-acme",
  "day_service": "2026-05-11#Amazon EC2",
  "day":         "2026-05-11",
  "service":     "Amazon EC2",
  "cost_cents":  4200
}
```

The `#` separator is chosen because it does not appear in any AWS
service name returned by Cost Explorer today, and the writer rejects
service names containing it so a future drift would surface loudly.

### Two access patterns become bounded Queries

1. **"All services for tenant T on day D"**

   ```python
   KeyConditionExpression =
       Key("tenant_id").eq(T) & Key("day_service").begins_with(f"{D}#")
   ```

2. **"All (day, service) rows for tenant T across a window"**

   ```python
   KeyConditionExpression =
       Key("tenant_id").eq(T)
       & Key("day_service").between(f"{from_iso}#", f"{prev_iso}#￿")
   ```

   The trailing `￿` sentinel keeps every real service-name string
   inside the upper bound while excluding `to_date` itself.

The third historical pattern, "all tenants on day D", continues to use
`Scan` with a `begins_with` filter on `day_service` (today bounded by
N tenants times M services per day, well under the 1 MB scan budget).
A future `DayIndex` GSI is still deferred; the access pattern lives
in `tenant_rollup.py`'s docstring + admin-state Terraform comment.

### Aggregator queries CE with a two-dimensional GroupBy

`aggregate_day` now calls `ce.get_cost_and_usage` with

```python
GroupBy = [
    {"Type": "TAG",       "Key": "tenant_id"},
    {"Type": "DIMENSION", "Key": "SERVICE"},
]
```

CE returns one Group per distinct `(tenant_id, service)` pair, encoded
as `Keys = ["tenant_id$<value>", "<service-name>"]`. The parser splits
on the first `$` for the tenant slot (keeping the prior robustness to
a future TAG-key rename) and reads the second key verbatim for the
service.

`AggregationSummary` gains a `rows_written` field counting persisted
per-(tenant, service) rows; `tenants_written` continues to count
distinct tenants so the existing CloudWatch shape stays additive
rather than breaking.

### Migration posture: table replacement, no data migration

DynamoDB does not support in-place sort key changes. The first apply
of this revision against `infra/dev/admin-state/` will replace the
`panakoes-dev-tenant-cost-rollup` table. Because the table is empty
in dev, no rows are lost; the replacement is operationally
indistinguishable from a fresh create. `deletion_protection_enabled`
stays `true`; an operator running this apply must accept the destroy
plan explicitly (Terraform shows `must be replaced`). For
non-empty production environments the runbook would export the rows
ahead of replacement and rehydrate; dev has nothing to export.

## Consequences

**Positive.**

- The admin SPA's per-tenant-per-service breakdown is now persistable
  without a parallel table or a join at read time. The read path is
  one Scan per day inside the route's window loop (the cost the prior
  shape already paid).
- Both the "all services for T on D" and "all (day, service) for T in
  window" patterns are bounded Queries on the existing partition key,
  not Scans. The hot path stays cheap.
- The composite key + `begins_with` pattern is the canonical DDB
  recipe for time-bucketed dimension breakdowns; future Panakoes
  tables that need similar shape can copy the convention without
  re-deriving it from first principles.
- The aggregator's two-dimensional GroupBy maxes out CE's per-call
  GroupBy budget (CE accepts at most two). Adding a third dimension
  (e.g., usage type) in the future would require splitting into
  multiple CE calls; that is a fair budget signal at the right place.
- The schema is forward-compatible with a future `DayIndex` GSI:
  hashing on `day_service` would let "rank tenants by cost on day X
  by service" run as a Query across all tenants without scanning.

**Negative.**

- The sort-key change requires table replacement. Safe in dev where
  the table is empty, but a non-trivial operation for any environment
  that has accumulated rollup data. The admin-state README captures
  the procedure (flip `deletion_protection_enabled`, apply, re-enable)
  and points production at an explicit export-rehydrate runbook
  before any prod-tier rollout.
- The aggregator now writes one row per (tenant, day, service) tuple
  instead of one row per (tenant, day). At realistic AWS service
  counts (~10-30 distinct services per tenant per day) this is a
  10-30x increase in row count. DynamoDB pay-per-request pricing keeps
  the cost negligible at dev scale; at production scale a sustained
  100 tenants * 30 services * 30 days = 90k rows per month is still
  pennies. The aggregator's CE call count does not change (one call
  per day) because the two-dimensional GroupBy returns all pairs in
  one response.
- The `_row_from_item` parser carries a fallback path for rows that
  lack the denormalized `day` / `service` attributes. This is
  defensive against pre-write-path tooling producing bare composite
  keys; ordinary writes through `put_rollup` always populate the
  denormalized columns. The fallback adds ~5 lines of code and one
  branch on every read.
- Service names from CE are surfaced verbatim into the response. CE
  returns canonical strings like `"Amazon Elastic Compute Cloud -
  Compute"`; the SPA already handles these in the by-service view, so
  no extra normalization is needed.

## Alternatives considered

**Option B: Separate `panakoes-dev-tenant-service-cost-rollup` table
with `(tenant_id HK, day#service RK)`.** Rejected. The keyspace and
sort-key shape are identical to Option A; the only difference is that
the old table stays around carrying a now-orthogonal projection
(per-tenant-per-day total without service dimension). That projection
is recoverable by summing across services on read, so the second table
is pure duplication. Two tables also doubles the aggregator's write
path and the route's read path while buying nothing the composite
sort key does not already buy.

**Option C: Single composite hash key `tenant_id#service` with
`day` as the sort key.** Rejected. This shape optimizes for "all days
for one (tenant, service) pair" (which the SPA does not need today)
but pessimizes "all services for one tenant on one day" (which the
SPA does need) into a Scan, since the (tenant, service) pairs each
become their own partition. Best for per-service-time-series queries;
worst for the two patterns we actually serve.

**Status quo plus a denormalized "service breakdown" attribute on
each daily row.** Considered. The aggregator could pack a JSON map
`{service: cents}` into a single attribute. DynamoDB would store it
fine; the route could deserialize and surface it on read. Rejected
because (a) it precludes per-(service, day) bounded Queries forever
(the service dimension becomes unindexable), (b) it puts schema
inside an attribute value, which violates the "let the database
understand your schema" principle every DDB cost-analyst interview
will probe, and (c) the size of a single packed attribute grows
unbounded with the number of distinct services.

**Add a GSI on `service` to the existing table.** Rejected. A GSI
adds throughput cost (PAY_PER_REQUEST GSIs are billed independently
of the base table) and only addresses the "all rows for one service
across tenants" pattern, which we have no current consumer for. The
composite sort key gives us the patterns we need without the GSI
operational tax.

## References

- `infra/dev/admin-state/main.tf` (the `tenant_cost_rollup` resource;
  sort key now `day_service`)
- `infra/dev/admin-state/README.md` (table inventory + re-apply
  procedure)
- `services/cost-api/src/panakoes_cost_api/tenant_rollup.py`
  (`TenantRollupStore` with `make_day_service_key`, `day_prefix`,
  updated `query_window` / `query_all_tenants_for_day` / `put_rollup`)
- `services/cost-api/src/panakoes_cost_api/routes/cost.py`
  (`_aggregate_window` now collects per-(tenant, service) totals and
  emits `services: tuple[ServiceCostBreakdown, ...]` on each
  `TenantCostRow`)
- `services/cost-api/src/panakoes_cost_api/models.py`
  (`ServiceCostBreakdown` model + `services` field on `TenantCostRow`)
- `services/cost-rollup-aggregator/src/panakoes_cost_rollup_aggregator/aggregator.py`
  (two-dimensional GroupBy + per-service writes + `rows_written`)
- `docs/design/admin-dashboard-tier-2-3.md` (the SPA surface this
  schema backs)
- ADR-031 (cost-api read-through cache; the by-tenant route remains a
  read-through cache consumer)
