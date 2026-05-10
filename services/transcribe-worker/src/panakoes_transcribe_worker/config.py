"""Environment-driven configuration for the Transcribe Worker Lambda.

Mirrors the `event-router` pattern: a frozen dataclass populated from
process env vars at first call, with required vars failing fast at
load time so an unconfigured deploy errors at the first invoke instead
of silently routing into the void.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved environment configuration for the worker Lambda."""

    ddb_ingestion_table: str
    audio_uploads_bucket: str
    aws_region: str


def load_settings() -> Settings:
    """Build a `Settings` instance from process environment variables.

    Lambda sets `AWS_REGION` automatically. The other two are required
    on the function configuration; absent values raise at startup so a
    misconfigured deploy fails at first invoke, not silently downstream.
    """
    table = os.environ.get("DDB_INGESTION_TABLE", "")
    bucket = os.environ.get("AUDIO_UPLOADS_BUCKET", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not table:
        raise RuntimeError("DDB_INGESTION_TABLE env var is required")
    if not bucket:
        raise RuntimeError("AUDIO_UPLOADS_BUCKET env var is required")
    return Settings(
        ddb_ingestion_table=table,
        audio_uploads_bucket=bucket,
        aws_region=region,
    )
