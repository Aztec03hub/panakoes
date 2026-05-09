"""Unit tests for `CostExplorerClientWrapper`.

These tests stub the boto3 ce client with a `MagicMock` so no real
AWS calls happen. The wrapper is exercised through its public surface
(`get_cost_by_service`) plus the throttle-retry / validation-mapping
behavior.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from panakoes_cost_api.cost_explorer import (
    CostExplorerClientWrapper,
    CostExplorerThrottledError,
    InvalidDateRangeError,
)
from panakoes_cost_api.models import DateRange


def _ce_response_two_services() -> dict[str, object]:
    """A canonical CE GetCostAndUsage response with two services."""
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-04-01", "End": "2026-05-01"},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                        "Metrics": {"UnblendedCost": {"Amount": "12.34", "Unit": "USD"}},
                    },
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.08", "Unit": "USD"}},
                    },
                ],
                "Total": {},
                "Estimated": False,
            }
        ]
    }


@pytest.fixture
def window() -> DateRange:
    return DateRange(from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))


@pytest.mark.unit
async def test_cost_explorer_get_cost_by_service_parses_response(window: DateRange) -> None:
    """A canonical CE response parses into `CostBreakdown` with sorted rows."""
    client = MagicMock()
    client.get_cost_and_usage.return_value = _ce_response_two_services()
    wrapper = CostExplorerClientWrapper(client=client)

    result = await wrapper.get_cost_by_service(window)

    assert result.from_date == window.from_date
    assert result.to_date == window.to_date
    assert result.currency == "USD"
    assert result.cache_hit is False
    assert len(result.services) == 2
    # Sorted descending by cost_cents.
    assert result.services[0].service == "Amazon Elastic Compute Cloud - Compute"
    assert result.services[0].cost_cents == 1234
    assert result.services[1].service == "Amazon Simple Storage Service"
    assert result.services[1].cost_cents == 108
    assert result.total_cents == 1342


@pytest.mark.unit
async def test_cost_explorer_handles_throttle_with_retry(
    window: DateRange, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ThrottlingExceptions then a success returns successfully."""
    # Patch sleep so the test runs instantly.
    import panakoes_cost_api.cost_explorer as ce_mod

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(ce_mod.asyncio, "sleep", _no_sleep)

    throttle = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        operation_name="GetCostAndUsage",
    )

    client = MagicMock()
    client.get_cost_and_usage.side_effect = [throttle, throttle, _ce_response_two_services()]
    wrapper = CostExplorerClientWrapper(client=client)

    result = await wrapper.get_cost_by_service(window)

    assert client.get_cost_and_usage.call_count == 3
    assert result.total_cents == 1342


@pytest.mark.unit
async def test_cost_explorer_throttle_past_budget_raises(
    window: DateRange, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three throttles in a row raises `CostExplorerThrottledError`."""
    import panakoes_cost_api.cost_explorer as ce_mod

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(ce_mod.asyncio, "sleep", _no_sleep)

    throttle = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        operation_name="GetCostAndUsage",
    )

    client = MagicMock()
    client.get_cost_and_usage.side_effect = [throttle, throttle, throttle]
    wrapper = CostExplorerClientWrapper(client=client)

    with pytest.raises(CostExplorerThrottledError):
        await wrapper.get_cost_by_service(window)


@pytest.mark.unit
async def test_cost_explorer_handles_invalid_date_range(window: DateRange) -> None:
    """A CE ValidationException maps to our `InvalidDateRangeError`."""
    validation = ClientError(
        error_response={
            "Error": {"Code": "ValidationException", "Message": "Start must be <= End"}
        },
        operation_name="GetCostAndUsage",
    )
    client = MagicMock()
    client.get_cost_and_usage.side_effect = validation
    wrapper = CostExplorerClientWrapper(client=client)

    with pytest.raises(InvalidDateRangeError, match="Start must be <= End"):
        await wrapper.get_cost_by_service(window)


@pytest.mark.unit
def test_date_range_rejects_inverted_window() -> None:
    """`DateRange(from > to)` raises ValueError at construction."""
    with pytest.raises(ValueError, match="is after to_date"):
        DateRange(from_date=date(2026, 5, 1), to_date=date(2026, 4, 1))
