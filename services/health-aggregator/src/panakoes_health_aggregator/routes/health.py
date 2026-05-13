"""HTTP routes for the health-aggregator service.

Routes:
- `GET /health`  -> `HealthSnapshot` (all services rolled up)
- `GET /services/{name}` -> `ServiceDetail` (single service)

The API Gateway forwards `/v1/health-aggregator/{proxy+}` to this
service with the `/v1/health-aggregator` prefix stripped (ADR-038
shape (c+)), so the SPA's `/v1/health-aggregator/health` resolves
to this router's `/health`.

All routes require an admin JWT.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from panakoes_auth_client import JwtClaims

from panakoes_health_aggregator.aggregator import HealthAggregator
from panakoes_health_aggregator.auth import require_admin
from panakoes_health_aggregator.dependencies import get_aggregator
from panakoes_health_aggregator.models import HealthSnapshot, ServiceDetail
from panakoes_health_aggregator.registry import get_service

logger = structlog.get_logger(__name__)

# Mounted at root because the gateway strips the `/v1/health-aggregator`
# prefix before forwarding. Local dev hits `/health` and
# `/services/{name}` directly.
router = APIRouter(tags=["health-aggregator"])


@router.get("/health", response_model=HealthSnapshot)
async def get_health_snapshot(
    claims: Annotated[JwtClaims, Depends(require_admin)],
    aggregator: Annotated[HealthAggregator, Depends(get_aggregator)],
) -> HealthSnapshot:
    """Return the live health snapshot for every monitored service."""
    logger.info("health_aggregator_snapshot_request", subject=claims.sub)
    return await aggregator.snapshot()


@router.get("/services/{name}", response_model=ServiceDetail)
async def get_service_detail(
    name: str,
    claims: Annotated[JwtClaims, Depends(require_admin)],
    aggregator: Annotated[HealthAggregator, Depends(get_aggregator)],
) -> ServiceDetail:
    """Return the detail payload for a single service with live metrics and logs."""
    entry = get_service(name)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown service: {name}",
        )
    logger.info(
        "health_aggregator_service_detail_request",
        subject=claims.sub,
        service=name,
    )
    return await aggregator.detail_for(entry)
