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
    """A fresh moto-backed tenant-cost-rollup table per test (post-ADR-040 shape)."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-tenant-cost-rollup",
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "day_service", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "day_service", "AttributeType": "S"},
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
    rollup_store.put_rollup(
        tenant_id="tenant-a",
        day=date(2026, 4, 15),
        service="Amazon EC2",
        cost_cents=12345,
    )
    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    assert len(rows) == 1
    assert rows[0].tenant_id == "tenant-a"
    assert rows[0].day == date(2026, 4, 15)
    assert rows[0].service == "Amazon EC2"
    assert isinstance(rows[0].cost_cents, int)
    assert rows[0].cost_cents == 12345


@pytest.mark.unit
def test_query_window_filters_by_inclusive_start_exclusive_end(
    rollup_store: TenantRollupStore,
) -> None:
    """`to_date` is exclusive: rows on `to_date` are NOT returned."""
    rollup_store.put_rollup("tenant-a", date(2026, 3, 31), "Amazon EC2", 100)
    rollup_store.put_rollup("tenant-a", date(2026, 4, 1), "Amazon EC2", 200)
    rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "Amazon EC2", 300)
    rollup_store.put_rollup("tenant-a", date(2026, 5, 1), "Amazon EC2", 400)  # excluded
    rollup_store.put_rollup("tenant-a", date(2026, 5, 2), "Amazon EC2", 500)

    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    days = {row.day for row in rows}
    assert days == {date(2026, 4, 1), date(2026, 4, 15)}
    total = sum(r.cost_cents for r in rows)
    assert total == 500


@pytest.mark.unit
def test_query_window_returns_one_row_per_service_per_day(
    rollup_store: TenantRollupStore,
) -> None:
    """A single tenant with multiple services on the same day returns one row per service."""
    rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "Amazon EC2", 1000)
    rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "Amazon S3", 250)
    rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "Amazon DynamoDB", 30)

    rows = rollup_store.query_window(
        tenant_id="tenant-a", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)
    )
    assert len(rows) == 3
    by_service = {row.service: row.cost_cents for row in rows}
    assert by_service == {"Amazon EC2": 1000, "Amazon S3": 250, "Amazon DynamoDB": 30}


@pytest.mark.unit
def test_query_all_tenants_for_day_returns_per_service_rows(
    rollup_store: TenantRollupStore,
) -> None:
    """Multi-tenant + multi-service fixture: only the requested day's rows return."""
    target_day = date(2026, 4, 15)
    other_day = date(2026, 4, 16)

    rollup_store.put_rollup("tenant-a", target_day, "Amazon EC2", 1000)
    rollup_store.put_rollup("tenant-a", target_day, "Amazon S3", 100)
    rollup_store.put_rollup("tenant-b", target_day, "Amazon EC2", 2500)
    rollup_store.put_rollup("tenant-c", target_day, "Amazon DynamoDB", 250)
    # Different-day rows that must be filtered out.
    rollup_store.put_rollup("tenant-a", other_day, "Amazon EC2", 9999)
    rollup_store.put_rollup("tenant-b", other_day, "Amazon EC2", 9999)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    by_tenant_service = {(row.tenant_id, row.service): row.cost_cents for row in rows}
    assert by_tenant_service == {
        ("tenant-a", "Amazon EC2"): 1000,
        ("tenant-a", "Amazon S3"): 100,
        ("tenant-b", "Amazon EC2"): 2500,
        ("tenant-c", "Amazon DynamoDB"): 250,
    }


@pytest.mark.unit
def test_put_rollup_rejects_negative_cents(
    rollup_store: TenantRollupStore,
) -> None:
    """A negative cost is a programming bug and must surface loudly."""
    with pytest.raises(ValueError, match="cost_cents must be non-negative"):
        rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "Amazon EC2", -1)


@pytest.mark.unit
def test_put_rollup_rejects_empty_service(
    rollup_store: TenantRollupStore,
) -> None:
    """An empty service produces a malformed sort key; reject at write time."""
    with pytest.raises(ValueError, match="service must be non-empty"):
        rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "", 100)


@pytest.mark.unit
def test_put_rollup_rejects_service_with_separator(
    rollup_store: TenantRollupStore,
) -> None:
    """A service name containing the `#` separator would corrupt the sort key."""
    with pytest.raises(ValueError, match="must not contain"):
        rollup_store.put_rollup("tenant-a", date(2026, 4, 15), "bad#service", 100)


@pytest.mark.unit
def test_percent_of_total_math_on_aggregated_rows(
    rollup_store: TenantRollupStore,
) -> None:
    """Percent-of-total uses integer-cents arithmetic with `round(x, 2)`."""
    target_day = date(2026, 4, 15)
    # One service per tenant so each tenant has one row.
    rollup_store.put_rollup("tenant-a", target_day, "Amazon EC2", 1000)
    rollup_store.put_rollup("tenant-b", target_day, "Amazon EC2", 2500)
    rollup_store.put_rollup("tenant-c", target_day, "Amazon EC2", 250)

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
    assert abs((pct_a + pct_b + pct_c) - 100.0) <= 0.05


@pytest.mark.unit
def test_percent_of_total_handles_zero_total_without_divide_by_zero(
    rollup_store: TenantRollupStore,
) -> None:
    """An all-zero-cost window must not divide by zero."""
    target_day = date(2026, 4, 15)
    rollup_store.put_rollup("tenant-a", target_day, "Amazon EC2", 0)
    rollup_store.put_rollup("tenant-b", target_day, "Amazon EC2", 0)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    total_cents = sum(r.cost_cents for r in rows)
    assert total_cents == 0

    for row in rows:
        percent = (row.cost_cents / total_cents * 100.0) if total_cents > 0 else 0.0
        assert percent == 0.0
