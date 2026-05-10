"""Unit tests for `panakoes_cost_rollup_aggregator.aggregator`.

The aggregator is the pure business logic the Lambda handler delegates
to. We exercise it against the real `TenantRollupStore` (moto-backed)
and a hand-rolled `FakeCEClient` so the test surface stays
production-shaped without spinning up a CE simulator.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from panakoes_cost_rollup_aggregator.aggregator import (
    UNTAGGED_TENANT_ID,
    _usd_to_cents,
    aggregate_day,
)
from tests.conftest import FakeCEClient, make_ce_response


@pytest.mark.unit
def test_aggregate_day_writes_one_row_per_tenant_group(
    rollup_store: Any,
) -> None:
    """3 tagged tenants + 1 untagged group writes 4 rollup rows."""
    target_day = date(2026, 5, 8)
    ce = FakeCEClient(
        responses=[
            make_ce_response(
                day_iso=target_day.isoformat(),
                tenant_groups=[
                    ("tenant-a", "1.23"),
                    ("tenant-b", "4.56"),
                    ("tenant-c", "0.10"),
                ],
                untagged_amount="2.50",
            ),
        ],
    )

    summary = aggregate_day(cast(Any, ce), rollup_store, target_day)

    assert summary.day == target_day
    assert summary.tenants_written == 4
    assert summary.untagged_cost_cents == 250
    assert summary.ce_calls == 1
    assert summary.duration_ms >= 0

    rows = rollup_store.query_all_tenants_for_day(target_day)
    by_tenant = {row.tenant_id: row.cost_cents for row in rows}
    assert by_tenant == {
        "tenant-a": 123,
        "tenant-b": 456,
        "tenant-c": 10,
        UNTAGGED_TENANT_ID: 250,
    }


@pytest.mark.unit
def test_aggregate_day_calls_ce_with_correct_window_and_groupby(
    rollup_store: Any,
) -> None:
    """The CE call uses start-inclusive / end-exclusive day window with TAG GroupBy."""
    target_day = date(2026, 5, 8)
    ce = FakeCEClient(
        responses=[make_ce_response(day_iso=target_day.isoformat(), tenant_groups=[])],
    )

    aggregate_day(cast(Any, ce), rollup_store, target_day)

    assert len(ce.calls) == 1
    kwargs = ce.calls[0]
    assert kwargs["TimePeriod"] == {"Start": "2026-05-08", "End": "2026-05-09"}
    assert kwargs["Granularity"] == "DAILY"
    assert kwargs["Metrics"] == ["UnblendedCost"]
    assert kwargs["GroupBy"] == [{"Type": "TAG", "Key": "tenant_id"}]


@pytest.mark.unit
def test_aggregate_day_empty_response_writes_no_rows(
    rollup_store: Any,
) -> None:
    """A CE response with zero groups writes zero rows and reports zero tenants."""
    target_day = date(2026, 5, 8)
    ce = FakeCEClient(
        responses=[make_ce_response(day_iso=target_day.isoformat(), tenant_groups=[])],
    )

    summary = aggregate_day(cast(Any, ce), rollup_store, target_day)

    assert summary.tenants_written == 0
    assert summary.untagged_cost_cents == 0
    assert rollup_store.query_all_tenants_for_day(target_day) == []


@pytest.mark.unit
def test_aggregate_day_re_run_is_idempotent_upsert(
    rollup_store: Any,
) -> None:
    """Re-running the same day overwrites without duplicating rows."""
    target_day = date(2026, 5, 8)
    ce_first = FakeCEClient(
        responses=[
            make_ce_response(
                day_iso=target_day.isoformat(),
                tenant_groups=[("tenant-a", "1.00")],
            ),
        ],
    )
    aggregate_day(cast(Any, ce_first), rollup_store, target_day)

    # Second run with the same numbers: row count and value unchanged.
    ce_second = FakeCEClient(
        responses=[
            make_ce_response(
                day_iso=target_day.isoformat(),
                tenant_groups=[("tenant-a", "1.00")],
            ),
        ],
    )
    aggregate_day(cast(Any, ce_second), rollup_store, target_day)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    assert len(rows) == 1
    assert rows[0].cost_cents == 100

    # Third run with a corrected (larger) number for the same day:
    # row converges to the latest CE answer, not summed across runs.
    ce_third = FakeCEClient(
        responses=[
            make_ce_response(
                day_iso=target_day.isoformat(),
                tenant_groups=[("tenant-a", "5.00")],
            ),
        ],
    )
    aggregate_day(cast(Any, ce_third), rollup_store, target_day)

    rows = rollup_store.query_all_tenants_for_day(target_day)
    assert len(rows) == 1
    assert rows[0].cost_cents == 500


@pytest.mark.unit
def test_aggregate_day_follows_ce_pagination(
    rollup_store: Any,
) -> None:
    """`NextPageToken` is followed across multiple CE calls."""
    target_day = date(2026, 5, 8)
    page_one = make_ce_response(
        day_iso=target_day.isoformat(),
        tenant_groups=[("tenant-a", "1.00")],
        next_page_token="page-2",
    )
    page_two = make_ce_response(
        day_iso=target_day.isoformat(),
        tenant_groups=[("tenant-b", "2.00")],
    )
    ce = FakeCEClient(responses=[page_one, page_two])

    summary = aggregate_day(cast(Any, ce), rollup_store, target_day)

    assert summary.ce_calls == 2
    assert summary.tenants_written == 2
    # Second call carried the token from the first.
    assert ce.calls[1].get("NextPageToken") == "page-2"
    # First call did not.
    assert "NextPageToken" not in ce.calls[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("amount", "expected_cents"),
    [
        ("0", 0),
        ("0.00", 0),
        ("0.01", 1),
        ("0.005", 1),  # ROUND_HALF_UP, NOT banker's rounding
        ("0.004", 0),
        ("1.234", 123),  # rounds down
        ("1.235", 124),  # ROUND_HALF_UP
        ("12.345", 1235),
        ("100", 10000),
        ("not-a-number", 0),  # malformed input -> 0, no crash
        ("-0.50", 0),  # negative refund clamped to 0
    ],
)
def test_usd_to_cents_rounds_half_up(amount: str, expected_cents: int) -> None:
    """Money math uses Decimal + ROUND_HALF_UP and clamps negatives to zero."""
    assert _usd_to_cents(amount) == expected_cents
