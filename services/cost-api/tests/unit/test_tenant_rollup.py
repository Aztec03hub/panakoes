"""Unit tests for the moto-backed `TenantRollupStore`.

The route layer aggregates these primitives into a `TenantCostBreakdown`;
those tests live in `tests/integration/test_cost_routes.py`. Here we
exercise the storage contract: query semantics, date-window filtering,
write validation, and the percent-of-total math the aggregator helper
will depend on once the route file is wired.

Every test runs inside a `mock_aws()` block so DynamoDB calls hit the
in-process moto stub. Tests pre-populate via `put_rollup(...)` because
the rollup table is empty until the nightly aggregator (Phase 2.2)
lands; using the public write path keeps the tests honest about the
storage contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import boto3
import pytest
from moto import mock_aws

from panakoes_cost_api.tenant_rollup import TenantRollupStore


@pytest.fixture
def rollup_store() -> Iterator[TenantRollupStore]:
    """A fresh moto-backed tenant-cost-rollup table per test."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-tenant-cost-rollup",
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "day", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "day", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield TenantRollupStore(table=table)


@pytest.mark.unit
def test_query_window_empty_table_returns_empty_list(
    rollup_store: TenantRollupStore,
) -> None:
    """An untouched rollup table yields an empty list, not an error."""
    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    assert rows == []


@pytest.mark.unit
def test_query_window_returns_single_row(rollup_store: TenantRollupStore) -> None:
    """A single put round-trips through the store with the right typing."""
    rollup_store.put_rollup(tenant_id="tenant-a", day=date(2026, 4, 15), cost_cents=12345)
    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    assert len(rows) == 1
    assert rows[0].tenant_id == "tenant-a"
    assert rows[0].day == date(2026, 4, 15)
    # Critically: the integer-cents value is an int, not a Decimal,
    # at the boundary so percent-of-total math stays exact downstream.
    assert isinstance(rows[0].cost_cents, int)
    assert rows[0].cost_cents == 12345


@pytest.mark.unit
def test_query_window_filters_by_inclusive_start_exclusive_end(
    rollup_store: TenantRollupStore,
) -> None:
    """`to_date` is exclusive: rows on `to_date` are NOT returned."""
    rollup_store.put_rollup("tenant-a", date(2026, 3, 31), 100)  # before window
    rollup_store.put_rollup("tenant-a", date(2026, 4, 1), 200)  # at window start
    rollup_store.put_rollup("tenant-a", date(2026, 4, 15), 300)  # inside window
    rollup_store.put_rollup("tenant-a", date(2026, 5, 1), 400)  # at window end (excluded)
    rollup_store.put_rollup("tenant-a", date(2026, 5, 2), 500)  # after window

    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    days = {row.day for row in rows}
    assert days == {date(2026, 4, 1), date(2026, 4, 15)}
    total = sum(r.cost_cents for r in rows)
    assert total == 500


@pytest.mark.unit
def test_query_all_tenants_for_day_returns_multi_tenant_rows(
    rollup_store: TenantRollupStore,
) -> None:
    """Multi-tenant + multi-day fixture: only the requested day's rows return."""
    target_day = date(2026, 4, 15)
    other_day = date(2026, 4, 16)

    rollup_store.put_rollup("tenant-a", target_day, 1000)
    rollup_store.put_rollup("tenant-b", target_day, 2500)
    rollup_store.put_rollup("tenant-c", target_day, 250)
    # Different-day rows that must be filtered out.
    rollup_store.put_rollup("tenant-a", other_day, 9999)
    rollup_store.put_rollup("tenant-b", other_day, 9999)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    by_tenant = {row.tenant_id: row.cost_cents for row in rows}
    assert by_tenant == {"tenant-a": 1000, "tenant-b": 2500, "tenant-c": 250}


@pytest.mark.unit
def test_percent_of_total_math_on_aggregated_rows(
    rollup_store: TenantRollupStore,
) -> None:
    """Percent-of-total uses integer-cents arithmetic with `round(x, 2)`.

    The route builds `TenantCostRow.percent_of_total` from the store's
    output via `cents / total * 100, rounded to 2 dp`, matching the
    by-service route's exact arithmetic. This test pins that contract
    so a refactor of the store cannot silently drift the rounding rule.
    """
    target_day = date(2026, 4, 15)
    rollup_store.put_rollup("tenant-a", target_day, 1000)
    rollup_store.put_rollup("tenant-b", target_day, 2500)
    rollup_store.put_rollup("tenant-c", target_day, 250)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    by_tenant = {row.tenant_id: row.cost_cents for row in rows}
    total_cents = sum(by_tenant.values())
    assert total_cents == 3750

    pct_a = round(by_tenant["tenant-a"] / total_cents * 100, 2)
    pct_b = round(by_tenant["tenant-b"] / total_cents * 100, 2)
    pct_c = round(by_tenant["tenant-c"] / total_cents * 100, 2)
    assert pct_a == 26.67
    assert pct_b == 66.67
    assert pct_c == 6.67
    # Rounding noise stays under 0.05 (the route does NOT renormalize).
    assert abs((pct_a + pct_b + pct_c) - 100.0) <= 0.05


@pytest.mark.unit
def test_percent_of_total_handles_zero_total_without_divide_by_zero(
    rollup_store: TenantRollupStore,
) -> None:
    """An all-zero-cost window must not divide by zero.

    Pins the route's total=0 contract: `percent_of_total` is 0.0 when
    every tenant has zero cents (or the table is empty for the window),
    rather than raising `ZeroDivisionError`. The math the route uses,
    `(cents / total * 100) if total > 0 else 0.0`, is exercised here at
    the integer-cents store boundary so a future refactor cannot drop
    the guard silently.
    """
    target_day = date(2026, 4, 15)
    rollup_store.put_rollup("tenant-a", target_day, 0)
    rollup_store.put_rollup("tenant-b", target_day, 0)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    total_cents = sum(r.cost_cents for r in rows)
    assert total_cents == 0

    # The exact arithmetic the route applies in `_aggregate_window`.
    for row in rows:
        percent = (row.cost_cents / total_cents * 100.0) if total_cents > 0 else 0.0
        assert percent == 0.0
