"""Environment-driven configuration for the Event Router Lambda.

Lambda runtime auto-injects `AWS_REGION`; the other variables are set on
the function (Terraform-managed) and read on first import. We resolve
defaults that are obviously wrong in production (`""`) so an
unconfigured deploy fails fast at startup rather than silently routing
events into the void.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved environment configuration for the Lambda."""

    ddb_ingestion_table: str
    eventbridge_bus_name: str
    aws_region: str
    metric_namespace: str = "panakoes/dev/event-router"
    event_source: str = "panakoes.ingest"
    event_detail_type: str = "AudioUploaded"


def load_settings() -> Settings:
    """Build a `Settings` instance from process environment variables.

    Lambda sets `AWS_REGION` automatically. The other two are required
    on the function configuration; absent values raise at startup.
    """
    table = os.environ.get("DDB_INGESTION_TABLE", "")
    bus = os.environ.get("EVENTBRIDGE_BUS_NAME", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not table:
        raise RuntimeError("DDB_INGESTION_TABLE env var is required")
    if not bus:
        raise RuntimeError("EVENTBRIDGE_BUS_NAME env var is required")
    return Settings(
        ddb_ingestion_table=table,
        eventbridge_bus_name=bus,
        aws_region=region,
    )
