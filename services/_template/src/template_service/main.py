"""FastAPI entrypoint for the Panakoes template service.

Provides a minimal application with a `/health` endpoint and structured
logging via `structlog`. New services copy this module and replace the
service identifier to inherit the same shape.

This module is also the canonical example for wiring the
`panakoes-otel` shared instrumentation library: `configure()` runs in
the FastAPI lifespan, the auto-instrumentation hooks attach to the
running app + boto3 + httpx, and `shutdown()` flushes exporters on
graceful shutdown.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

from template_service.config import Settings

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
    """Wire OpenTelemetry on startup and flush on shutdown.

    The `panakoes-otel` library is idempotent, so reload-driven double
    startups are safe. Tests pin `OTEL_SDK_DISABLED=true` so no real
    exporter sockets open during the suite.
    """
    otel_configure(
        service_name=settings.service_name,
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_fastapi(app)
    instrument_boto3()
    instrument_httpx()
    try:
        yield
    finally:
        otel_shutdown()


app = FastAPI(title=f"panakoes-{settings.service_name}", lifespan=lifespan)


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
        "template_service.main:app",
        host="0.0.0.0",  # noqa: S104  # bind on all interfaces inside the container
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
