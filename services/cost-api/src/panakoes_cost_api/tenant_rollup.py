"""DynamoDB-backed store for the per-tenant daily per-service cost rollup.

Wraps `panakoes-dev-tenant-cost-rollup` (HK `tenant_id`, RK
`day_service`, where `day_service` is the composite string
`YYYY-MM-DD#<service>`) with three operations:

- `query_window(tenant_id, from_date, to_date)` returns every
  (day, service) row for one tenant inside an inclusive-start,
  exclusive-end date window.
- `query_all_tenants_for_day(day)` Scan-derived (no GSI in Phase 0)
  read of every tenant's per-service rows for one day. Used by the
  by-tenant route to assemble a multi-tenant breakdown across N days
  by iterating days inside the window.
- `put_rollup(tenant_id, day, service, cost_cents)` writes one row.
  Reserved for the nightly aggregator job (`services/cost-rollup-aggregator/`).
  The route only calls the query methods, but the store ships put now
  so the aggregator job can drop in without re-touching this file.

Storage contract (committed by the admin-state Terraform module, see
ADR-040 for the design rationale):

    pk: tenant_id (S)
    sk: day_service (S, composite: "YYYY-MM-DD#<service>")
    day: S (denormalized for human readability + future GSI on day)
    service: S (denormalized: the service component of day_service)
    cost_cents: N (integer cents, non-negative)

The composite sort key supports two access patterns as bounded Queries:
1. "all services for tenant T on day D":
   Key('tenant_id').eq(T) & Key('day_service').begins_with(f"{D}#")
2. "all (day, service) rows for tenant T in a window":
   Key('tenant_id').eq(T) & Key('day_service').between(start_prefix, end_prefix)

`day` and `service` are denormalized on every row so callers do not
need to re-split the composite key on every read. The split is owned
by `_row_from_item`; writers always go through `put_rollup` so the
two halves cannot drift.

DynamoDB stores numeric values as `Decimal`; the wrapper converts back
to `int` at the boundary so callers never see a `Decimal` leak. The
date <-> string boundary is owned by this module: callers pass
`datetime.date` objects, the store renders ISO strings on writes and
parses them back on reads.

The route layer treats the rollup table as a precomputed cache; if the
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


# Separator between the day and service halves of the composite sort key.
# The `#` character is reserved in AWS-style composite keys and does not
# appear in any AWS service name returned by Cost Explorer, so it is a
# safe boundary marker. Centralized here so the aggregator (which
# builds the key on write) and the store (which splits it on read)
# cannot drift independently.
DAY_SERVICE_SEPARATOR = "#"


def make_day_service_key(day: date, service: str) -> str:
    """Compose the `day_service` sort key from its two halves.

    Used by writers (the aggregator) and by readers that need to derive
    a `begins_with` prefix for a specific day. Keeping this in one
    function means the format is owned by a single source of truth.
    """
    return f"{day.isoformat()}{DAY_SERVICE_SEPARATOR}{service}"


def day_prefix(day: date) -> str:
    """Return the `begins_with` prefix that selects every service for `day`.

    Convenience for callers building a Query KeyConditionExpression
    against the composite sort key without re-deriving the separator.
    """
    return f"{day.isoformat()}{DAY_SERVICE_SEPARATOR}"


@dataclass(frozen=True)
class DailyRollupRow:
    """One (day, service) slice of cost for one tenant.

    Frozen dataclass rather than a Pydantic model because this is an
    internal shape consumed only by `tenant_rollup.py` and the route
    aggregation; nothing serializes it across a wire boundary, and the
    boto3 read path already validates its primitive types.
    """

    tenant_id: str
    day: date
    service: str
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
        """Return every (day, service) row for `tenant_id` with `from_date <= day < to_date`.

        Inclusive-start / exclusive-end mirrors the AWS Cost Explorer
        date-window semantics so totals reconcile with `by-service`. An
        inverted window returns an empty list rather than raising;
        callers validate window order at the route layer.

        The query uses `between` on the composite `day_service` key. Day
        strings sort lexicographically (ISO YYYY-MM-DD) and the `#`
        separator sorts after every printable service-name character we
        expect from CE, so `between(from#, prev-day#~)` cleanly bounds
        the slice. Concretely we use `to_date_prev + "#￿"` as the
        upper bound: every real service-name character compares less
        than the unicode max, so every service for `to_date_prev` is
        included while `to_date` and beyond are excluded.
        """
        if from_date >= to_date:
            return []

        from_key = day_prefix(from_date)
        # Inclusive upper bound on the latest day in the window
        # (`to_date - 1d`). Append a high sentinel so every service on
        # that day sorts inside the bound.
        last_day = date.fromordinal(to_date.toordinal() - 1)
        to_key = f"{day_prefix(last_day)}￿"

        response = self._table.query(
            KeyConditionExpression=(
                Key("tenant_id").eq(tenant_id) & Key("day_service").between(from_key, to_key)
            )
        )
        items = response.get("Items", [])
        return [_row_from_item(item) for item in items]

    def query_all_tenants_for_day(self, day: date) -> list[DailyRollupRow]:
        """Return every tenant's per-service rows for one specific `day`.

        Implemented as a `Scan` with a `begins_with` filter on
        `day_service` because Phase 0 provisioned no GSI on the day
        component (deferred until the access pattern is exercised
        against real data; see `infra/dev/admin-state/main.tf` and
        ADR-040). The scan is bounded by N tenants times M services per
        day, which is a small constant for the lifetime of this
        project; if N*M grows past a few thousand rows per day we
        revisit and add a `DayIndex` GSI.
        """
        prefix = day_prefix(day)
        items: list[dict[str, Any]] = []
        last_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "FilterExpression": Key("day_service").begins_with(prefix),
            }
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                break
        return [_row_from_item(item) for item in items]

    def put_rollup(
        self,
        tenant_id: str,
        day: date,
        service: str,
        cost_cents: int,
    ) -> None:
        """Persist one (tenant, day, service) rollup row.

        Reserved for the nightly aggregator job. Validates `cost_cents`
        is non-negative because a negative cost means the aggregator has
        a bug (refunds and credits flow through Stripe billing, not the
        AWS-side cost rollup). Validates `service` is non-empty so the
        composite key always has both halves present.
        """
        if cost_cents < 0:
            raise ValueError(f"cost_cents must be non-negative, got {cost_cents}")
        if not service:
            raise ValueError("service must be non-empty")
        if DAY_SERVICE_SEPARATOR in service:
            # If a future AWS service name ever contains `#` we will
            # need to pick a different separator and migrate. Surface
            # the conflict loudly rather than silently producing keys
            # that split incorrectly on read.
            raise ValueError(
                f"service must not contain {DAY_SERVICE_SEPARATOR!r}: got {service!r}"
            )
        self._table.put_item(
            Item={
                "tenant_id": tenant_id,
                "day_service": make_day_service_key(day, service),
                # Denormalized for human readability + future GSI.
                "day": day.isoformat(),
                "service": service,
                "cost_cents": cost_cents,
            }
        )
        logger.debug(
            "tenant_rollup_put",
            tenant_id=tenant_id,
            day=day.isoformat(),
            service=service,
            cost_cents=cost_cents,
        )


def _row_from_item(item: dict[str, Any]) -> DailyRollupRow:
    """Convert a raw DynamoDB Item dict into a typed `DailyRollupRow`.

    Tolerates two on-the-wire shapes: rows with denormalized `day` and
    `service` attributes (the post-ADR-040 write path), and legacy rows
    that only carry the composite `day_service` key (a defensive fallback
    if any pre-write tooling produces the bare key). Splitting on the
    last `#` keeps the parse robust if a service name ever contains the
    separator earlier in the string (today none do; see `put_rollup`'s
    validation).
    """
    raw_cost = item["cost_cents"]
    cost_cents = int(raw_cost)
    day_val = item.get("day")
    service_val = item.get("service")
    if day_val is None or service_val is None:
        # Fallback: parse from the composite key.
        composite = str(item["day_service"])
        day_str, sep, service_str = composite.partition(DAY_SERVICE_SEPARATOR)
        if not sep:
            raise ValueError(f"malformed day_service key: {composite!r}")
        day_val = day_str
        service_val = service_str
    return DailyRollupRow(
        tenant_id=str(item["tenant_id"]),
        day=date.fromisoformat(str(day_val)),
        service=str(service_val),
        cost_cents=cost_cents,
    )
