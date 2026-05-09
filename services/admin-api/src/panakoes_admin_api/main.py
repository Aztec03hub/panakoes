"""FastAPI entrypoint for the Panakoes admin-api service.

Phase 0 skeleton: only `/health` is wired. Phase 1 lands the Tier 3
safety pattern (typed confirmation + audit-before-AND-after + step-up
MFA + idempotency key) along with the first three lifecycle operations
(terminate session, revoke API credentials, force password reset).

The full surface area is documented in
`docs/design/admin-dashboard-tier-2-3.md` and phased in
`docs/design/tier-2-3-implementation-plan.md`.
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

from panakoes_admin_api.config import Settings

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
    """Wire OpenTelemetry on startup and flush on shutdown."""
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
        "panakoes_admin_api.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
