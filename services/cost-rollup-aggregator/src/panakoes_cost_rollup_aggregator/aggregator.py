"""Pure aggregation function for the nightly tenant cost rollup.

Separated from `handler.py` so the business logic is testable without
the Lambda runtime contract. The handler wires AWS clients + env vars
and delegates to `aggregate_day` here.

Cost Explorer contract:
- `TimePeriod` is start-inclusive, end-exclusive (matches the rest of
  the cost-api surface; see `models.DateRange`).
- `Granularity = "DAILY"` returns one `ResultsByTime` entry per day.
  Aggregating one day at a time means there is exactly one entry to
  unpack and no cross-day arithmetic to get wrong.
- `GroupBy` is two-dimensional post-ADR-040: `[{TAG: tenant_id},
  {DIMENSION: SERVICE}]`. CE returns one `Group` per distinct
  (tenant, service) pair with `Keys = ["tenant_id$<value>",
  "<service>"]`. Untagged spend lands in groups whose first key is
  `"tenant_id$"` (the trailing `$` is CE's representation of the
  empty tag value). We map that to the synthetic tenant id
  `__untagged__` so the rollup table at least carries the bucket;
  the by-tenant route surfaces it as a row with per-service slices,
  which is operationally useful (operator sees the magnitude AND the
  composition of unattributed spend).

Pagination: CE responses for `GetCostAndUsage` carry `NextPageToken`
when results exceed one page. We follow the token to drain the full
window. For one day at a few-hundred tenants worst case we expect
exactly one page, but the loop costs us nothing and removes a future
silent-truncation foot-gun.

Money math: CE returns `Amount` as a USD float string. We convert it
to `Decimal` before multiplying by 100 and rounding `ROUND_HALF_UP`
to the nearest integer cent. Doing the math in `Decimal` keeps
fractional-cent values like `$0.005` deterministic (becomes `1` cent,
not `0`), which matters for low-cost dev environments where many
tenants will show single-digit cent values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from mypy_boto3_ce.client import CostExplorerClient
    from panakoes_cost_api.tenant_rollup import TenantRollupStore


logger = structlog.get_logger(__name__)

# Synthetic tenant id used when CE returns spend that has no `tenant_id`
# tag value attached. Surfacing this as a real row (rather than dropping
# it) is the point: it tells operators "you have $X of unattributed
# spend, go fix the tagging policy." The downstream by-tenant route
# renders this id in the table verbatim until display-name lookup wires
# in.
UNTAGGED_TENANT_ID = "__untagged__"


@dataclass(frozen=True)
class AggregationSummary:
    """Structured success summary returned from `aggregate_day`.

    Renders to a JSON-shaped dict via `as_dict` so the handler can dump
    it through structlog into Lambda's CloudWatch log stream where it is
    queryable via Logs Insights. The `day` is rendered ISO so the log
    line is immediately groupable by date.

    `rows_written` counts the per-(tenant, service) rows persisted (one
    per CE group, post-ADR-040). `tenants_written` counts distinct
    tenants for backward compatibility with the prior log shape so
    CloudWatch alarms keyed on that field continue to fire.
    """

    day: date
    tenants_written: int
    rows_written: int
    untagged_cost_cents: int
    ce_calls: int
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "tenants_written": self.tenants_written,
            "rows_written": self.rows_written,
            "untagged_cost_cents": self.untagged_cost_cents,
            "ce_calls": self.ce_calls,
            "duration_ms": self.duration_ms,
        }


def aggregate_day(
    ce_client: CostExplorerClient,
    store: TenantRollupStore,
    day: date,
) -> AggregationSummary:
    """Pull one day of per-tenant spend from CE and write it to the rollup table.

    `day` is the UTC calendar day to aggregate. The CE window is
    `[day, day + 1d)` (start-inclusive, end-exclusive). Every tenant
    group in the response (including the synthetic untagged group) is
    written via `store.put_rollup`; re-runs are upserts by virtue of
    DynamoDB `put_item`, so retries and late-arriving CE refreshes
    converge on the latest CE answer for the day.
    """
    started_at = datetime.now()
    next_day = day + timedelta(days=1)
    ce_kwargs: dict[str, Any] = {
        "TimePeriod": {
            "Start": day.isoformat(),
            "End": next_day.isoformat(),
        },
        "Granularity": "DAILY",
        "Metrics": ["UnblendedCost"],
        # Two-dimensional GroupBy post-ADR-040: per (tenant, service).
        # CE accepts at most two GroupBy entries; tenant_id by TAG and
        # SERVICE by DIMENSION fill exactly that budget.
        "GroupBy": [
            {"Type": "TAG", "Key": "tenant_id"},
            {"Type": "DIMENSION", "Key": "SERVICE"},
        ],
    }

    rows_written = 0
    tenants_seen: set[str] = set()
    untagged_cost_cents = 0
    ce_calls = 0
    next_token: str | None = None

    while True:
        if next_token is not None:
            ce_kwargs["NextPageToken"] = next_token
        # `get_cost_and_usage` returns a TypedDict in boto3 stubs, but the
        # downstream parser is intentionally dict-shaped so it stays
        # decoupled from the stub package. Cast at the boundary.
        response = cast(dict[str, Any], ce_client.get_cost_and_usage(**ce_kwargs))
        ce_calls += 1

        for group in _iter_groups(response):
            tenant_id, service, cost_cents = _parse_group(group)
            store.put_rollup(
                tenant_id=tenant_id, day=day, service=service, cost_cents=cost_cents
            )
            rows_written += 1
            tenants_seen.add(tenant_id)
            if tenant_id == UNTAGGED_TENANT_ID:
                untagged_cost_cents += cost_cents

        next_token = response.get("NextPageToken")
        if not next_token:
            break

    duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
    summary = AggregationSummary(
        day=day,
        tenants_written=len(tenants_seen),
        rows_written=rows_written,
        untagged_cost_cents=untagged_cost_cents,
        ce_calls=ce_calls,
        duration_ms=duration_ms,
    )
    logger.info("cost_rollup_aggregate_day", **summary.as_dict())
    return summary


def _iter_groups(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the per-tenant `Group` list from a `GetCostAndUsage` response.

    With `Granularity=DAILY` and a one-day window, CE returns exactly
    one `ResultsByTime` entry. Defensively cope with the (impossible
    but cheap) zero-entry case so a misconfigured CE call cannot blow
    up with an `IndexError` at 02:00 UTC.
    """
    results = response.get("ResultsByTime") or []
    if not results:
        return []
    first = results[0]
    groups = first.get("Groups") or []
    return list(groups)


UNKNOWN_SERVICE = "__unknown__"


def _parse_group(group: dict[str, Any]) -> tuple[str, str, int]:
    """Convert one CE `Group` entry into a `(tenant_id, service, cost_cents)` triple.

    With the two-dimensional GroupBy (`TAG tenant_id`, `DIMENSION
    SERVICE`), CE encodes `Keys` as a two-element list:
    `["tenant_id$<value>", "<service-name>"]`. The first key is the tag
    in the same `tenant_id$<value>` shape we parsed before; the second
    is the raw AWS service name (no prefix). We split on the first `$`
    for the tenant slot to stay robust if CE renames the tag key in our
    config, and we use a `__unknown__` sentinel if CE ever returns a
    group with a missing or empty service key so the writer still has a
    non-empty service to compose into the sort key.
    """
    keys = group.get("Keys") or []
    raw_tenant_key = str(keys[0]) if len(keys) > 0 else ""
    _, _, tag_value = raw_tenant_key.partition("$")
    tenant_id = tag_value if tag_value else UNTAGGED_TENANT_ID

    raw_service = str(keys[1]) if len(keys) > 1 else ""
    service = raw_service if raw_service else UNKNOWN_SERVICE

    metrics = group.get("Metrics") or {}
    unblended = metrics.get("UnblendedCost") or {}
    raw_amount = unblended.get("Amount", "0")
    cost_cents = _usd_to_cents(str(raw_amount))
    return tenant_id, service, cost_cents


def _usd_to_cents(amount: str) -> int:
    """Convert a USD float-string into integer cents with ROUND_HALF_UP.

    Uses `Decimal` so common cases like `0.005` round to `1` cent
    deterministically rather than landing on the nearest even integer
    (which is what `round()` does in Python). CE may return very small
    negative numbers for refunds in some accounts; we clamp to zero
    because the rollup table contract is `cost_cents >= 0`. Real
    refund accounting belongs in Stripe / billing, not in the AWS-side
    rollup.
    """
    try:
        as_decimal = Decimal(amount)
    except (ArithmeticError, ValueError):
        # Defensive: a malformed CE response should not crash the run.
        # Log and treat as zero so the rest of the day still aggregates.
        logger.warning("cost_rollup_unparseable_amount", amount=amount)
        return 0
    cents = (as_decimal * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if cents < 0:
        return 0
    return int(cents)
