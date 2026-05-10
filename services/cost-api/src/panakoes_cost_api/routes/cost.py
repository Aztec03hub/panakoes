"""HTTP route: GET /api/v1/cost/by-service.

Returns the per-service cost breakdown for a date window, served from
the DynamoDB cache when fresh and falling back to AWS Cost Explorer on
cache miss. Admin role required.

The route depends on:
- `require_admin`: 401 / 403 gating.
- `get_cost_cache`: factory the lifespan owns; tests override.
- `get_cost_explorer`: factory the lifespan owns; tests override.

Failure mapping:
- `InvalidDateRangeError` (from CE or `DateRange`) -> 400.
- `CostExplorerThrottledError` -> 502 (transient).
- Any other CE `ClientError` propagated up -> 502 (CE upstream failure).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_auth_client import JwtClaims

from panakoes_cost_api.alert_state import AlertStateStore
from panakoes_cost_api.anomaly_detector import AnomalyDetector
from panakoes_cost_api.auth import require_admin
from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import (
    CostExplorerClientWrapper,
    CostExplorerThrottledError,
    InvalidDateRangeError,
)
from panakoes_cost_api.dependencies import (
    get_alert_state,
    get_anomaly_detector,
    get_cost_cache,
    get_cost_explorer,
    get_tenant_cost_cache,
    get_tenant_rollup,
)
from panakoes_cost_api.models import (
    CacheKey,
    CostAnomaly,
    CostAnomalyList,
    CostBreakdown,
    CostForecast,
    DateRange,
    TenantCostBreakdown,
    TenantCostRow,
)
from panakoes_cost_api.tenant_rollup import TenantRollupStore

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])


@router.get("/by-service", response_model=CostBreakdown)
async def get_cost_by_service(
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    claims: Annotated[JwtClaims, Depends(require_admin)],
    cache: Annotated[CostCache[CostBreakdown], Depends(get_cost_cache)],
    ce: Annotated[CostExplorerClientWrapper, Depends(get_cost_explorer)],
) -> CostBreakdown:
    """Return AWS spend broken down by service for a date window.

    Query parameters:
    - `from`: inclusive start (ISO date).
    - `to`:   exclusive end (ISO date).

    The response includes a `cache_hit` flag the dashboard surfaces so
    operators can tell at a glance whether the page was served from
    DynamoDB or paid a CE round trip. `queried_at` is the wall-clock
    instant the response was assembled (server-trusted).
    """
    try:
        window = DateRange(from_date=from_date, to_date=to_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    key = CacheKey(query_kind="by-service", from_date=from_date, to_date=to_date)

    async def fetch() -> CostBreakdown:
        return await ce.get_cost_by_service(window)

    try:
        breakdown = await cache.cache_or_fetch(key, fetch)
    except InvalidDateRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CostExplorerThrottledError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cost Explorer is throttled, try again shortly",
        ) from exc
    except ClientError as exc:
        # Cost Explorer returned an unrecoverable error (AccessDenied,
        # DataUnavailable, etc.). Map to 502 so the operator sees an
        # upstream failure rather than a generic 500.
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("cost_api_ce_error", code=code, subject=claims.sub)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cost Explorer error: {code}",
        ) from exc

    logger.info(
        "cost_api_by_service",
        subject=claims.sub,
        from_date=str(from_date),
        to_date=str(to_date),
        cache_hit=breakdown.cache_hit,
        services=len(breakdown.services),
    )
    return breakdown


# Cache TTL for by-tenant rollups. Longer than by-service because the
# rollup table is precomputed by the nightly aggregator, so the upstream
# data only changes once per day. 6 hours buys roughly 4 cache writes
# per UTC day in the worst case while still bounding staleness if the
# aggregator job is re-run after an incident. The DynamoDB TTL on the
# cache row plus the request-time read-through means the dashboard
# never serves data older than 6 hours past its precomputed source.
TENANT_CACHE_TTL_SECONDS = 6 * 3600


@router.get("/by-tenant", response_model=TenantCostBreakdown)
async def get_cost_by_tenant(
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    claims: Annotated[JwtClaims, Depends(require_admin)],
    cache: Annotated[CostCache[TenantCostBreakdown], Depends(get_tenant_cost_cache)],
    rollup: Annotated[TenantRollupStore, Depends(get_tenant_rollup)],
) -> TenantCostBreakdown:
    """Return AWS spend broken down by tenant for a date window.

    Reads from `panakoes-dev-tenant-cost-rollup` (populated by the
    nightly aggregator job, Phase 2.2). The route aggregates daily rows
    into per-tenant totals across the window, sorts descending by cost,
    and computes `percent_of_total` against the window's grand total
    using integer-cents arithmetic.

    The rollup table being empty (no aggregator run yet, or no activity
    in the window) is a normal case: the response carries `tenants=()`
    and `total_cents=0` rather than 404, matching the by-service
    "zero-spend window" semantics the dashboard already handles.
    """
    try:
        window = DateRange(from_date=from_date, to_date=to_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    key = CacheKey(query_kind="by-tenant", from_date=from_date, to_date=to_date)

    async def fetch() -> TenantCostBreakdown:
        return _aggregate_window(rollup, window)

    breakdown = await cache.cache_or_fetch(key, fetch, ttl_seconds=TENANT_CACHE_TTL_SECONDS)

    logger.info(
        "cost_api_by_tenant",
        subject=claims.sub,
        from_date=str(from_date),
        to_date=str(to_date),
        cache_hit=breakdown.cache_hit,
        tenants=len(breakdown.tenants),
    )
    return breakdown


def _aggregate_window(
    rollup: TenantRollupStore,
    window: DateRange,
) -> TenantCostBreakdown:
    """Walk the window day-by-day, sum per tenant, build the response.

    Implementation note: we iterate days inside the route because Phase
    0 deliberately did not provision a `DayIndex` GSI. Each day is one
    Scan with a `FilterExpression`, bounded by N-tenants-per-day. For
    the dashboard's typical window (last 7-30 days at a few hundred
    tenants worst case) this is well below DynamoDB's 1MB scan budget.
    See `tenant_rollup.py` for the GSI deferral rationale.
    """
    # Daily rows summed per tenant_id.
    per_tenant: dict[str, int] = {}
    cursor = window.from_date
    while cursor < window.to_date:
        for row in rollup.query_all_tenants_for_day(cursor):
            per_tenant[row.tenant_id] = per_tenant.get(row.tenant_id, 0) + row.cost_cents
        cursor = cursor + timedelta(days=1)

    total_cents = sum(per_tenant.values())
    rows: list[TenantCostRow] = []
    for tenant_id, cents in per_tenant.items():
        # Integer-cents percent: cents / total * 100, rounded to 2 dp.
        # Matches the by-service route's exact arithmetic so the two
        # views reconcile to within rounding noise.
        percent = (cents / total_cents * 100.0) if total_cents > 0 else 0.0
        rows.append(
            TenantCostRow(
                tenant_id=tenant_id,
                # Display name defaults to the tenant id; the future
                # auth-db join will populate this with the org name.
                display_name=tenant_id,
                cost_cents=cents,
                percent_of_total=round(percent, 2),
            )
        )
    rows.sort(key=lambda r: r.cost_cents, reverse=True)

    return TenantCostBreakdown(
        from_date=window.from_date,
        to_date=window.to_date,
        currency="USD",
        tenants=tuple(rows),
        total_cents=total_cents,
        cache_hit=False,
        queried_at=datetime.now(UTC),
    )


# Allowed horizon-days values for the `/forecast` route. Mirrors
# `COST_FORECAST_HORIZONS` in `services/admin/src/lib/api.ts`; the two
# constants must stay in sync. Anything outside this set rejects with
# 400 so the route surface stays predictable for the dashboard's
# dropdown and any future scripted callers.
ALLOWED_FORECAST_HORIZONS: frozenset[int] = frozenset({7, 14, 30, 60, 90})


@router.get("/forecast", response_model=CostForecast)
async def get_cost_forecast(
    claims: Annotated[JwtClaims, Depends(require_admin)],
    ce: Annotated[CostExplorerClientWrapper, Depends(get_cost_explorer)],
    horizon_days: Annotated[
        int,
        Query(
            description=(
                "Number of consecutive days to forecast starting today (UTC). "
                "Must be one of 7, 14, 30, 60, 90."
            ),
        ),
    ] = 30,
) -> CostForecast:
    """Return CE's daily cost forecast for the next `horizon_days`.

    Backed by `boto3 ce.GetCostForecast` (`Granularity=DAILY`,
    `Metric=UNBLENDED_COST`, `PredictionIntervalLevel=95`). Each
    response bucket carries the mean predicted spend plus the 95%
    prediction-interval lower / upper bounds, all in integer cents.
    The route does not cache the response: CE's forecast model
    refreshes daily, the call is cheap, and the dashboard's horizon
    dropdown re-fires the request on every change so a stale cache
    would mostly hurt freshness with little CE-cost savings.
    """
    if horizon_days not in ALLOWED_FORECAST_HORIZONS:
        allowed = ", ".join(str(h) for h in sorted(ALLOWED_FORECAST_HORIZONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"horizon_days must be one of {allowed}",
        )

    try:
        forecast = await ce.get_cost_forecast(horizon_days)
    except InvalidDateRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CostExplorerThrottledError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cost Explorer is throttled, try again shortly",
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("cost_api_forecast_ce_error", code=code, subject=claims.sub)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cost Explorer error: {code}",
        ) from exc

    logger.info(
        "cost_api_forecast",
        subject=claims.sub,
        horizon_days=horizon_days,
        buckets=len(forecast.buckets),
    )
    return forecast


# Detection-window length the anomalies route uses when callers ask
# for `active_only=false`. 30 days matches the AWS Cost Anomaly
# Detection default look-back and is short enough to keep the CE call
# under typical rate budgets while still surfacing recent anomalies.
ANOMALY_DETECTION_WINDOW_DAYS = 30


@router.get("/anomalies", response_model=CostAnomalyList)
async def get_cost_anomalies(
    claims: Annotated[JwtClaims, Depends(require_admin)],
    alert_state: Annotated[AlertStateStore, Depends(get_alert_state)],
    detector: Annotated[AnomalyDetector, Depends(get_anomaly_detector)],
    detector_filter: Annotated[
        str | None,
        Query(
            alias="detector",
            description="Optional exact-match filter on the detector name.",
        ),
    ] = None,
    active_only: Annotated[
        bool,
        Query(
            description=(
                "When true (default), return only the dedup-active alert-state rows. "
                "When false, also detect fresh anomalies from Cost Explorer for the "
                "last 30 days and mark each one as suppressed if its dedup signature "
                "matches an active alert-state row."
            ),
        ),
    ] = True,
    response_kind: Annotated[
        Literal["list"],
        Query(
            alias="kind",
            description="Reserved for future shape variants; only 'list' is supported today.",
        ),
    ] = "list",
) -> CostAnomalyList:
    """Return the current cost-anomaly feed.

    The dashboard's "Cost anomalies" page consumes this route. When
    `active_only=true` (the default) the route reads the alert-state
    table directly so the response is fast and CE-rate-limit-safe.

    When `active_only=false` the route additionally fetches
    detector-fresh anomalies from Cost Explorer for the last 30 days,
    deduplicates against the active alert-state rows by `signature`,
    and marks duplicates as `suppressed=true`. That lets operators see
    the full detector picture (fresh + suppressed) when investigating
    a paged anomaly's history.

    The route returns an empty list (rather than a 5xx) when no Cost
    Anomaly Monitor is configured upstream. The dashboard renders that
    as a clean empty state with a hint to set one up. See the
    follow-up section of `.agent-runs/<this-run>.md` for the Terraform
    that creates the monitor.
    """
    # `response_kind` is reserved for future use but consumed here so
    # the OpenAPI docs reflect the parameter's existence. Discard the
    # local binding to satisfy strict type checkers.
    del response_kind

    active = detector.read_active_alerts(detector=detector_filter)
    active_signatures = {a.signature for a in active}

    anomalies: list[CostAnomaly]
    if active_only:
        anomalies = active
    else:
        today = date.today()
        window = DateRange(
            from_date=today - timedelta(days=ANOMALY_DETECTION_WINDOW_DAYS),
            to_date=today,
        )
        try:
            fresh = await detector.detect_from_cost_explorer(window)
        except CostExplorerThrottledError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cost Explorer is throttled, try again shortly",
            ) from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            logger.warning("cost_api_anomaly_ce_error", code=code, subject=claims.sub)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Cost Explorer error: {code}",
            ) from exc

        # Dedup: anomalies whose signature matches an active alert
        # are surfaced as `suppressed=true`. Active alerts themselves
        # appear once, with `suppressed=false`.
        merged: dict[str, CostAnomaly] = {a.signature: a for a in active}
        if detector_filter is not None:
            fresh = [a for a in fresh if a.detector == detector_filter]
        for f in fresh:
            if f.signature in active_signatures:
                merged[f.signature] = f.model_copy(update={"suppressed": True})
            elif f.signature not in merged:
                merged[f.signature] = f
        anomalies = list(merged.values())

    # `alert_state` injected for symmetry with future write paths
    # (e.g. an admin acknowledge-alert endpoint). Today the route
    # only reads via the detector; the dependency keeps lifespan
    # wiring observable from the route signature.
    del alert_state

    response = CostAnomalyList(
        anomalies=tuple(anomalies),
        queried_at=datetime.now(UTC),
    )
    logger.info(
        "cost_api_anomalies",
        subject=claims.sub,
        active_only=active_only,
        detector=detector_filter,
        count=len(anomalies),
    )
    return response
