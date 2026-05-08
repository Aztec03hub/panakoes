"""FastAPI entrypoint for the Panakoes template service.

Provides a minimal application with a `/health` endpoint and structured
logging via `structlog`. New services copy this module and replace the
service identifier to inherit the same shape.
"""

from __future__ import annotations

import logging

import structlog
import uvicorn
from fastapi import FastAPI
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

app = FastAPI(title=f"panakoes-{settings.service_name}")


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
