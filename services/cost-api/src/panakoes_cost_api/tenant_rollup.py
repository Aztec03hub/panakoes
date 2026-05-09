"""DynamoDB-backed store for the per-tenant daily cost rollup.

Wraps `panakoes-dev-tenant-cost-rollup` (HK `tenant_id`, RK `day`) with
three operations:

- `query_window(tenant_id, from_date, to_date)` returns every daily row
  for one tenant inside an inclusive-start, exclusive-end date window.
- `query_all_tenants_for_day(day)` Scan-derived (no GSI in Phase 0)
  read of every tenant's row for one day. Used by the by-tenant route
  to assemble a multi-tenant breakdown across N days by iterating days
  inside the window.
- `put_rollup(tenant_id, day, cost_cents)` writes one row. Reserved
  for the nightly aggregator job (Phase 2.2). The route only calls the
  query methods, but the store ships put now so the aggregator job can
  drop in without re-touching this file.

Storage contract (committed by the Phase 0 Terraform module):

    pk: tenant_id (S)
    sk: day (S, ISO date YYYY-MM-DD)
    cost_cents: N (integer cents, non-negative)

DynamoDB stores numeric values as `Decimal`; the wrapper converts back
to `int` at the boundary so callers never see a `Decimal` leak. The
date <-> string boundary is owned by this module: callers pass
`datetime.date` objects, the store renders ISO strings on writes and
parses them back on reads.

Phase 2 deliberately keeps this layer query-only-against-the-table
rather than computing rollups on demand. The plan's variable+fixed
hybrid math lives in the future nightly aggregator (Day 2.2 work). The
route layer treats the rollup table as a precomputed cache; if the
table is empty (no aggregator run yet, or no activity in the window)
the route returns an empty breakdown rather than synthesizing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog
from boto3.dynamodb.conditions import Key

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DailyRollupRow:
    """One day of cost for one tenant, as stored in the rollup table.

    Frozen dataclass rather than a Pydantic model because this is an
    internal shape consumed only by `tenant_rollup.py` and the route
    aggregation; nothing serializes it across a wire boundary, and the
    boto3 read path already validates its primitive types.
    """

    tenant_id: str
    day: date
    cost_cents: int


class TenantRollupStore:
    """DynamoDB-backed reader / writer for the tenant cost rollup table.

    The `Table` resource is injected so tests can hand in a moto-mocked
    table without monkey-patching boto3. In production the lifespan
    constructs one store per service lifetime.
    """

    def __init__(self, table: Table) -> None:
        self._table = table

    def query_window(
        self,
        tenant_id: str,
        from_date: date,
        to_date: date,
    ) -> list[DailyRollupRow]:
        """Return every row for `tenant_id` with `from_date <= day < to_date`.

        Inclusive-start / exclusive-end mirrors the AWS Cost Explorer
        date-window semantics so totals reconcile with `by-service`. An
        inverted window returns an empty list rather than raising;
        callers validate window order at the route layer.
        """
        if from_date >= to_date:
            return []

        # DynamoDB BETWEEN is inclusive on both ends. Subtract one day on
        # the upper bound to express our exclusive-end semantic, but only
        # when from_date < to_date (already enforced above). Day strings
        # sort lexicographically because they are ISO YYYY-MM-DD.
        from_key = from_date.isoformat()
        to_key = _previous_day_iso(to_date)

        response = self._table.query(
            KeyConditionExpression=(
                Key("tenant_id").eq(tenant_id) & Key("day").between(from_key, to_key)
            )
        )
        items = response.get("Items", [])
        return [_row_from_item(item) for item in items]

    def query_all_tenants_for_day(self, day: date) -> list[DailyRollupRow]:
        """Return every tenant's row for one specific `day`.

        Implemented as a `Scan` with a `FilterExpression` because Phase 0
        provisioned no GSI on `day` (deferred until the access pattern is
        exercised against real data; see `infra/dev/admin-state/main.tf`
        Phase 2 comment). The scan is bounded by N tenants per day, which
        is a small constant for the lifetime of this project; if N grows
        past a few hundred tenants we revisit and add a `DayIndex` GSI.
        """
        day_key = day.isoformat()
        items: list[dict[str, Any]] = []
        last_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "FilterExpression": Key("day").eq(day_key),
            }
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                break
        return [_row_from_item(item) for item in items]

    def put_rollup(self, tenant_id: str, day: date, cost_cents: int) -> None:
        """Persist one daily rollup row.

        Reserved for the nightly aggregator job. Validates `cost_cents`
        is non-negative because a negative cost means the aggregator has
        a bug (refunds and credits flow through Stripe billing, not the
        AWS-side cost rollup).
        """
        if cost_cents < 0:
            raise ValueError(
                f"cost_cents must be non-negative, got {cost_cents}"
            )
        self._table.put_item(
            Item={
                "tenant_id": tenant_id,
                "day": day.isoformat(),
                "cost_cents": cost_cents,
            }
        )
        logger.debug(
            "tenant_rollup_put",
            tenant_id=tenant_id,
            day=day.isoformat(),
            cost_cents=cost_cents,
        )


def _row_from_item(item: dict[str, Any]) -> DailyRollupRow:
    """Convert a raw DynamoDB Item dict into a typed `DailyRollupRow`."""
    # boto3 returns DynamoDB N attributes as `Decimal`; coerce to plain
    # `int` so percent-of-total math downstream stays exact and the
    # JSON serialization stays Python-native.
    raw_cost = item["cost_cents"]
    cost_cents = int(raw_cost)
    return DailyRollupRow(
        tenant_id=str(item["tenant_id"]),
        day=date.fromisoformat(str(item["day"])),
        cost_cents=cost_cents,
    )


def _previous_day_iso(d: date) -> str:
    """ISO string for the day immediately before `d`.

    Used to translate our exclusive-end window into DynamoDB's inclusive
    BETWEEN bound. Implemented via `date.toordinal()` to avoid importing
    `datetime.timedelta` for one subtraction (keeps the module surface
    small).
    """
    return date.fromordinal(d.toordinal() - 1).isoformat()
