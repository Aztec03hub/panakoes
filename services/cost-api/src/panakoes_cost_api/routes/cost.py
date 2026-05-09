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

from datetime import date
from typing import Annotated

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_auth_client import JwtClaims

from panakoes_cost_api.auth import require_admin
from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import (
    CostExplorerClientWrapper,
    CostExplorerThrottledError,
    InvalidDateRangeError,
)
from panakoes_cost_api.dependencies import get_cost_cache, get_cost_explorer
from panakoes_cost_api.models import CacheKey, CostBreakdown, DateRange

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])


@router.get("/by-service", response_model=CostBreakdown)
async def get_cost_by_service(
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    claims: Annotated[JwtClaims, Depends(require_admin)],
    cache: Annotated[CostCache, Depends(get_cost_cache)],
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
