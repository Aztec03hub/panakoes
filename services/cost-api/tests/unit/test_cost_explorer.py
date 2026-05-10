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


def _ce_forecast_response_three_days() -> dict[str, object]:
    """A canonical CE GetCostForecast response with three daily buckets."""
    return {
        "Total": {"Amount": "9.00", "Unit": "USD"},
        "ForecastResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-05-12", "End": "2026-05-13"},
                "MeanValue": "3.00",
                "PredictionIntervalLowerBound": "2.50",
                "PredictionIntervalUpperBound": "3.50",
            },
            {
                "TimePeriod": {"Start": "2026-05-10", "End": "2026-05-11"},
                "MeanValue": "2.00",
                "PredictionIntervalLowerBound": "1.50",
                "PredictionIntervalUpperBound": "2.60",
            },
            {
                "TimePeriod": {"Start": "2026-05-11", "End": "2026-05-12"},
                "MeanValue": "4.00",
                "PredictionIntervalLowerBound": "3.20",
                "PredictionIntervalUpperBound": "4.80",
            },
        ],
    }


@pytest.mark.unit
async def test_cost_explorer_get_cost_forecast_parses_response() -> None:
    """A canonical CE forecast response parses into `CostForecast`, sorted ascending."""
    client = MagicMock()
    client.get_cost_forecast.return_value = _ce_forecast_response_three_days()
    wrapper = CostExplorerClientWrapper(client=client)

    result = await wrapper.get_cost_forecast(horizon_days=7)

    assert result.horizon_days == 7
    assert result.model == "ce-builtin"
    assert len(result.buckets) == 3
    # Ascending by date.
    assert result.buckets[0].bucket_date == date(2026, 5, 10)
    assert result.buckets[0].predicted_cost_cents == 200
    assert result.buckets[0].lower_bound_cents == 150
    assert result.buckets[0].upper_bound_cents == 260
    assert result.buckets[1].bucket_date == date(2026, 5, 11)
    assert result.buckets[1].predicted_cost_cents == 400
    assert result.buckets[2].bucket_date == date(2026, 5, 12)
    assert result.buckets[2].predicted_cost_cents == 300

    # Verify the call shape: today/today+7 window, daily granularity, 95% PI.
    call_kwargs = client.get_cost_forecast.call_args.kwargs
    assert call_kwargs["Granularity"] == "DAILY"
    assert call_kwargs["Metric"] == "UNBLENDED_COST"
    assert call_kwargs["PredictionIntervalLevel"] == 95
    assert "Start" in call_kwargs["TimePeriod"]
    assert "End" in call_kwargs["TimePeriod"]


@pytest.mark.unit
async def test_cost_explorer_forecast_floors_negative_lower_bound_at_zero() -> None:
    """CE can emit slightly-negative lower bounds for low-spend forecasts; floor at 0."""
    client = MagicMock()
    client.get_cost_forecast.return_value = {
        "ForecastResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-05-10", "End": "2026-05-11"},
                "MeanValue": "0.10",
                "PredictionIntervalLowerBound": "-0.05",
                "PredictionIntervalUpperBound": "0.30",
            },
        ],
    }
    wrapper = CostExplorerClientWrapper(client=client)

    result = await wrapper.get_cost_forecast(horizon_days=7)

    assert len(result.buckets) == 1
    assert result.buckets[0].lower_bound_cents == 0
    assert result.buckets[0].predicted_cost_cents == 10
    assert result.buckets[0].upper_bound_cents == 30


@pytest.mark.unit
async def test_cost_explorer_forecast_maps_validation_error() -> None:
    """A CE ValidationException on forecast maps to `InvalidDateRangeError`."""
    validation = ClientError(
        error_response={
            "Error": {"Code": "ValidationException", "Message": "Start must be in the future"}
        },
        operation_name="GetCostForecast",
    )
    client = MagicMock()
    client.get_cost_forecast.side_effect = validation
    wrapper = CostExplorerClientWrapper(client=client)

    with pytest.raises(InvalidDateRangeError, match="Start must be in the future"):
        await wrapper.get_cost_forecast(horizon_days=7)


@pytest.mark.unit
def test_date_range_rejects_inverted_window() -> None:
    """`DateRange(from > to)` raises ValueError at construction."""
    with pytest.raises(ValueError, match="is after to_date"):
        DateRange(from_date=date(2026, 5, 1), to_date=date(2026, 4, 1))
