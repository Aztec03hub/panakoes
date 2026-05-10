"""FastAPI entrypoint for the Panakoes Ingestion API."""

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
)
from panakoes_otel import (
    shutdown as otel_shutdown,
)

from panakoes_ingestion_api.config import Settings
from panakoes_ingestion_api.routes import health, ingestion, transcribe

settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire OpenTelemetry on startup and flush on shutdown.

    The ingestion API talks to S3 + DynamoDB via boto3 (instrumented)
    and serves HTTP via FastAPI (instrumented). It does not use
    `httpx` for outbound calls, so we skip that hook.
    """
    otel_configure(
        service_name=settings.service_name,
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_fastapi(app)
    instrument_boto3()
    try:
        yield
    finally:
        otel_shutdown()


app = FastAPI(title=f"panakoes-{settings.service_name}", lifespan=lifespan)
app.include_router(health.router)
app.include_router(ingestion.router)
app.include_router(transcribe.router)


def main() -> None:
    """Run the service with `uvicorn` for direct invocation."""
    uvicorn.run(
        "panakoes_ingestion_api.main:app",
        host="0.0.0.0",  # noqa: S104  # bind on all interfaces inside the container
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
