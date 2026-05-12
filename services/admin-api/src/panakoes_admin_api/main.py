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

from panakoes_admin_api.batch_client import Boto3BatchTerminator
from panakoes_admin_api.config import Settings
from panakoes_admin_api.eventbridge import Boto3EventBridgePublisher
from panakoes_admin_api.lifecycle_state import LifecycleStateStore
from panakoes_admin_api.routes.audit_read import router as audit_read_router
from panakoes_admin_api.routes.lifecycle import router as lifecycle_router

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

    # Long-lived AWS handles. Tests override the matching dependency
    # factories with moto-backed equivalents so this code never touches
    # real AWS in CI.
    ddb = boto3.resource("dynamodb", region_name=settings.aws_region)
    app.state.audit_table = ddb.Table(settings.audit_log_table)
    app.state.streaming_sessions_table = ddb.Table(settings.streaming_sessions_table)
    app.state.ingestion_table = ddb.Table(settings.ingestion_table)
    app.state.tenants_table = ddb.Table(settings.tenants_table)
    app.state.api_keys_table = ddb.Table(settings.api_keys_table)
    app.state.lifecycle_state = LifecycleStateStore(
        table=ddb.Table(settings.lifecycle_state_table)
    )
    events_client = boto3.client("events", region_name=settings.aws_region)
    app.state.eventbridge_publisher = Boto3EventBridgePublisher(
        client=events_client, bus_name=settings.events_bus_name
    )
    batch_client = boto3.client("batch", region_name=settings.aws_region)
    app.state.batch_client = Boto3BatchTerminator(client=batch_client)

    try:
        yield
    finally:
        otel_shutdown()


# Interactive docs (`/docs`, `/redoc`) and the live `/openapi.json`
# endpoint are gated by `ENABLE_OPENAPI_DOCS`. Defaults to on for dev
# so contributors can poke at the schema in a browser; production
# deploys set the env var to `false` so the docs surface never
# reaches the public internet. The checked-in
# `services/admin-api/openapi.json` artifact is the canonical schema
# for client codegen regardless of this toggle.
_docs_url = "/docs" if settings.enable_openapi_docs else None
_redoc_url = "/redoc" if settings.enable_openapi_docs else None
_openapi_url = "/openapi.json" if settings.enable_openapi_docs else None

app = FastAPI(
    title=f"panakoes-{settings.service_name}",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)
app.include_router(lifecycle_router)
app.include_router(audit_read_router)


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
