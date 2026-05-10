"""Async-friendly wrapper around the boto3 Cost Explorer client.

Cost Explorer is synchronous in boto3, so the async wrapper here uses
`asyncio.to_thread()` to keep FastAPI's event loop unblocked while CE
calls (which routinely take 1-3 seconds) are in flight.

The wrapper provides one operation today, `get_cost_by_service()`, which
runs `GetCostAndUsage` with `Granularity=MONTHLY`, `Metrics=["UnblendedCost"]`,
`GroupBy=[{Type: DIMENSION, Key: SERVICE}]`. CE returns the by-service
totals already aggregated; we parse them into our internal
`CostBreakdown` shape and sort descending by cost.

Failure modes:
- `ThrottlingException`: retried with exponential backoff (3 attempts,
  base 0.5s, jitter half a base interval). Past 3 attempts the wrapper
  re-raises so the route layer can map the failure to a 502.
- `DataUnavailableException`: re-raised as-is. CE returns this for
  windows that include the current day before its first daily refresh;
  the route layer treats this as a 502 (transient) rather than 500.
- `ValidationException` for date format / range: mapped to our own
  `InvalidDateRangeError` so the route layer can return 400 cleanly.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from typing import TYPE_CHECKING, Any

import structlog
from botocore.exceptions import ClientError

from panakoes_cost_api.models import (
    CostBreakdown,
    CostByService,
    CostForecast,
    DateRange,
    ForecastBucket,
)

if TYPE_CHECKING:
    from mypy_boto3_ce.client import CostExplorerClient


logger = structlog.get_logger(__name__)


THROTTLE_RETRY_ATTEMPTS = 3
THROTTLE_RETRY_BASE_SECONDS = 0.5


class CostExplorerError(Exception):
    """Base class for cost-api Cost Explorer errors."""


class InvalidDateRangeError(CostExplorerError):
    """Raised when CE rejects the requested date range (400-class error)."""


class CostExplorerThrottledError(CostExplorerError):
    """Raised when CE throttles past the retry budget."""


class CostExplorerClientWrapper:
    """Async-friendly wrapper around boto3's CE client.

    The boto3 client is injected so tests can hand in a stubbed instance
    without patching boto3. In production, the route layer constructs one
    wrapper per service lifetime and reuses it.
    """

    def __init__(self, client: CostExplorerClient) -> None:
        self._client = client

    async def get_cost_by_service(self, window: DateRange) -> CostBreakdown:
        """Return the by-service breakdown for the given window."""
        ce_kwargs: dict[str, Any] = {
            "TimePeriod": {
                "Start": window.from_date.isoformat(),
                "End": window.to_date.isoformat(),
            },
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        }
        response = await self._call_with_retry("get_cost_and_usage", ce_kwargs)
        return self._parse_response(response, window)

    async def get_cost_forecast(self, horizon_days: int) -> CostForecast:
        """Return CE's daily cost forecast for the next `horizon_days`.

        Calls `ce.GetCostForecast` with `Granularity=DAILY`,
        `Metric=UNBLENDED_COST` (consistent with `get_cost_by_service`),
        and `PredictionIntervalLevel=95` so each bucket carries a 95%
        confidence band. CE requires `Start` to be today or later and
        `End` to be strictly after `Start`; we anchor `Start` at today
        UTC and `End` at `today + horizon_days`. The wrapper handles
        throttling + validation mapping uniformly with the by-service
        path, so the route layer can map errors to HTTP codes via the
        same `CostExplorerThrottledError` / `InvalidDateRangeError` /
        `ClientError` ladder.
        """
        today = datetime.now(UTC).date()
        end = today + timedelta(days=horizon_days)
        ce_kwargs: dict[str, Any] = {
            "TimePeriod": {
                "Start": today.isoformat(),
                "End": end.isoformat(),
            },
            "Granularity": "DAILY",
            "Metric": "UNBLENDED_COST",
            "PredictionIntervalLevel": 95,
        }
        response = await self._call_with_retry("get_cost_forecast", ce_kwargs)
        return self._parse_forecast_response(response, horizon_days)

    @staticmethod
    def _parse_forecast_response(
        response: dict[str, Any],
        horizon_days: int,
    ) -> CostForecast:
        """Convert a CE GetCostForecast response into our `CostForecast` shape."""
        buckets: list[ForecastBucket] = []
        for entry in response.get("ForecastResultsByTime", []):
            time_period = entry.get("TimePeriod", {})
            start_str = time_period.get("Start")
            if not start_str:
                continue
            bucket_date = date_cls.fromisoformat(start_str)
            mean_str = entry.get("MeanValue", "0")
            lower_str = entry.get("PredictionIntervalLowerBound", mean_str)
            upper_str = entry.get("PredictionIntervalUpperBound", mean_str)
            # CE returns dollars as decimal strings; convert to integer
            # cents and floor at 0 because CE can emit very small
            # negative lower bounds for low-spend forecasts.
            mean_cents = max(round(float(mean_str) * 100), 0)
            lower_cents = max(round(float(lower_str) * 100), 0)
            upper_cents = max(round(float(upper_str) * 100), 0)
            buckets.append(
                # Constructed via `model_validate` rather than kwargs so
                # both the field name (`bucket_date`) and the JSON alias
                # (`date`) work; the pydantic-mypy plugin only sees the
                # field name on the typed signature, but we want this
                # construction site to read in the alias for symmetry
                # with the wire contract.
                ForecastBucket.model_validate(
                    {
                        "date": bucket_date,
                        "predicted_cost_cents": mean_cents,
                        "lower_bound_cents": lower_cents,
                        "upper_bound_cents": upper_cents,
                    }
                )
            )
        buckets.sort(key=lambda b: b.bucket_date)
        return CostForecast(
            horizon_days=horizon_days,
            buckets=tuple(buckets),
            model="ce-builtin",
            queried_at=datetime.now(UTC),
        )

    async def _call_with_retry(self, op_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Invoke `getattr(client, op_name)(**kwargs)` with throttle-retry."""
        last_error: Exception | None = None
        for attempt in range(1, THROTTLE_RETRY_ATTEMPTS + 1):
            try:
                op = getattr(self._client, op_name)
                return await asyncio.to_thread(op, **kwargs)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code == "ThrottlingException" and attempt < THROTTLE_RETRY_ATTEMPTS:
                    delay = (THROTTLE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))) * (
                        1.0 + random.uniform(0, 0.5)  # noqa: S311  # not security-sensitive
                    )
                    logger.warning(
                        "cost_explorer_throttled_retrying",
                        attempt=attempt,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    last_error = exc
                    continue
                if code == "ThrottlingException":
                    raise CostExplorerThrottledError(
                        f"Cost Explorer threw ThrottlingException after "
                        f"{THROTTLE_RETRY_ATTEMPTS} attempts"
                    ) from exc
                if code == "ValidationException":
                    raise InvalidDateRangeError(
                        exc.response.get("Error", {}).get("Message", "invalid date range")
                    ) from exc
                raise
        # Defensive: the loop should always either return or raise.
        raise CostExplorerError("unreachable: retry loop exited without return") from last_error

    @staticmethod
    def _parse_response(response: dict[str, Any], window: DateRange) -> CostBreakdown:
        """Convert a CE GetCostAndUsage response into our `CostBreakdown` shape."""
        rows: list[CostByService] = []
        results_by_time = response.get("ResultsByTime", [])
        # CE returns one ResultsByTime entry per granularity bucket; for
        # MONTHLY queries spanning a single month that is exactly one entry,
        # but we handle the multi-bucket case by summing across buckets.
        bucketed: dict[str, int] = {}
        currency = "USD"
        for bucket in results_by_time:
            for group in bucket.get("Groups", []):
                keys = group.get("Keys", [])
                if not keys:
                    continue
                service = keys[0]
                metrics = group.get("Metrics", {}).get("UnblendedCost", {})
                amount_str = metrics.get("Amount", "0")
                currency = metrics.get("Unit", currency)
                cost_cents = round(float(amount_str) * 100)
                bucketed[service] = bucketed.get(service, 0) + cost_cents

        total_cents = sum(bucketed.values())
        for service, cents in bucketed.items():
            percent = (cents / total_cents * 100.0) if total_cents > 0 else 0.0
            rows.append(
                CostByService(
                    service=service,
                    cost_cents=cents,
                    percent_of_total=round(percent, 2),
                )
            )
        rows.sort(key=lambda r: r.cost_cents, reverse=True)

        return CostBreakdown(
            from_date=window.from_date,
            to_date=window.to_date,
            currency=currency,
            services=tuple(rows),
            total_cents=total_cents,
            cache_hit=False,
            queried_at=datetime.now(UTC),
        )
