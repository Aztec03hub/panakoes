"""Liveness endpoint: `GET /health`."""

from __future__ import annotations

from fastapi import APIRouter

from panakoes_billing.models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return liveness and the service identifier."""
    return HealthResponse(status="ok", service="billing")
