"""FastAPI entrypoint for the health-aggregator service.

v0.1 wiring: `/health` snapshot + `/services/{name}` detail, both
admin-gated. AWS clients (ECS, ELBv2, CloudWatch Logs) are built
once per process in the lifespan and stashed on `app.state` for
the route layer to consume via dependency factories.

The shape mirrors `services/cost-api/src/panakoes_cost_api/main.py`
so OpenTelemetry, structured logging, and the lifespan-driven
exporter shutdown match every other Panakoes Python service.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import boto3
import structlog
import uvicorn
from botocore.config import Config
from fastapi import FastAPI
from fastapi.responses import Response
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

from panakoes_health_aggregator.aggregator import HealthAggregator
from panakoes_health_aggregator.clients.ecs import EcsClient, EcsClientProtocol
from panakoes_health_aggregator.clients.elbv2 import Elbv2Client, Elbv2ClientProtocol
from panakoes_health_aggregator.clients.logs import LogsClient, LogsClientProtocol
from panakoes_health_aggregator.config import Settings
from panakoes_health_aggregator.routes.health import router as health_router

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
    """Wire OpenTelemetry, build long-lived AWS clients, flush on shutdown."""
    otel_configure(
        service_name=settings.service_name,
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_fastapi(app)
    instrument_boto3()
    instrument_httpx()

    _boto_cfg = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
    ecs_raw = boto3.client("ecs", region_name=settings.aws_region, config=_boto_cfg)
    elbv2_raw = boto3.client("elbv2", region_name=settings.aws_region, config=_boto_cfg)
    logs_raw = boto3.client("logs", region_name=settings.aws_region, config=_boto_cfg)

    # boto3-stubs types `describe_services` etc. with TypedDict request shapes;
    # our Protocols use the simpler positional `dict[str, Any]` signature
    # that's far easier to satisfy from unit-test fakes. The cast bridges
    # the two without weakening either side.
    app.state.aggregator = HealthAggregator(
        ecs=EcsClient(
            client=cast(EcsClientProtocol, ecs_raw), cluster=settings.ecs_cluster
        ),
        elbv2=Elbv2Client(client=cast(Elbv2ClientProtocol, elbv2_raw)),
        logs=LogsClient(client=cast(LogsClientProtocol, logs_raw)),
        log_heartbeat_window_seconds=settings.log_heartbeat_window_seconds,
    )

    try:
        yield
    finally:
        otel_shutdown()


app = FastAPI(title=f"panakoes-{settings.service_name}", lifespan=lifespan)
app.include_router(health_router)


class LivenessResponse(BaseModel):
    """Schema returned by `/healthz`."""

    status: str
    service: str


@app.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """Container-liveness probe (not auth-gated, returns immediately).

    Distinct from `/health` (the admin-gated snapshot endpoint) so
    ECS / ALB target-health checks do not need a JWT. Mirrors the
    `cost-api` service's `/health` liveness path; renamed to
    `/healthz` here because `/health` is taken by the snapshot.
    """
    return LivenessResponse(status="ok", service=settings.service_name)


@app.options("/healthz")
async def healthz_options() -> Response:
    return Response(status_code=200)


@app.options("/health")
async def health_options() -> Response:
    return Response(status_code=200)


def main() -> None:
    """Run the service with `uvicorn` for direct invocation."""
    uvicorn.run(
        "panakoes_health_aggregator.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
