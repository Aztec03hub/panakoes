"""FastAPI entrypoint for the Panakoes Ingestion API."""

from __future__ import annotations

import logging

import structlog
import uvicorn
from fastapi import FastAPI

from panakoes_ingestion_api.config import Settings
from panakoes_ingestion_api.routes import health, ingestion

settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)

app = FastAPI(title=f"panakoes-{settings.service_name}")
app.include_router(health.router)
app.include_router(ingestion.router)


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
