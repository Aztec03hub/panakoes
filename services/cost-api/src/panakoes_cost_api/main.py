"""FastAPI entrypoint for the Panakoes cost-api service.

Phase 0 skeleton: only the `/health` endpoint is wired. Phase 1 lands
the by-service vertical slice (Cost Explorer client, cache layer,
auth-guarded `/cost/by-service` route). Phase 2 lands tenant rollup,
forecast, and anomalies.

Mirrors the canonical `services/_template/` wiring so OpenTelemetry,
structured logging, and the lifespan-driven exporter shutdown match
every other Panakoes Python service.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import boto3
import structlog
import uvicorn
from fastapi import FastAPI
from panakoes_otel import (
    configure as otel_configure,
)
from panakoes_otel import (
    instrument_boto3,
    instrument_fastapi,
    instrument_httpx,
)
from panakoes_otel import (
    shutdown as otel_shutdown,
)
from pydantic import BaseModel

from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.config import Settings
from panakoes_cost_api.cost_explorer import CostExplorerClientWrapper
from panakoes_cost_api.models import TenantCostBreakdown
from panakoes_cost_api.routes.cost import router as cost_router
from panakoes_cost_api.tenant_rollup import TenantRollupStore

settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire OpenTelemetry, build long-lived AWS clients, and flush on shutdown."""
    otel_configure(
        service_name=settings.service_name,
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_fastapi(app)
    instrument_boto3()
    instrument_httpx()

    # Build the boto3 clients once per process and stash them on app.state
    # so request-scoped dependencies (`get_cost_cache`, `get_cost_explorer`)
    # can hand them out without rebuilding. Tests override these dependencies
    # with moto-backed equivalents so this code never touches real AWS in CI.
    ddb = boto3.resource("dynamodb", region_name=settings.aws_region)
    cost_cache_table = ddb.Table(settings.cost_cache_table)
    app.state.cost_cache = CostCache(table=cost_cache_table)
    # Same DynamoDB table, separate CostCache instance hydrating the
    # tenant-flavor model. Keys are namespaced by `query_kind` so the
    # two caches never collide on the wire.
    app.state.tenant_cost_cache = CostCache(
        table=cost_cache_table, model_class=TenantCostBreakdown
    )
    app.state.tenant_rollup = TenantRollupStore(
        table=ddb.Table(settings.tenant_cost_rollup_table)
    )
    ce_client = boto3.client("ce", region_name=settings.aws_region)
    app.state.cost_explorer = CostExplorerClientWrapper(client=ce_client)

    try:
        yield
    finally:
        otel_shutdown()


app = FastAPI(title=f"panakoes-{settings.service_name}", lifespan=lifespan)
app.include_router(cost_router)


class HealthResponse(BaseModel):
    """Schema returned by the `/health` endpoint."""

    status: str
    service: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return liveness and the service identifier."""
    logger.debug("health_check", service=settings.service_name)
    return HealthResponse(status="ok", service=settings.service_name)


def main() -> None:
    """Run the service with `uvicorn` for direct invocation."""
    uvicorn.run(
        "panakoes_cost_api.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
