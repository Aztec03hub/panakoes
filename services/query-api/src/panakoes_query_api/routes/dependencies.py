"""Shared FastAPI dependency providers for the Query API routes.

Tests override these with fixtures that bind moto-backed adapters; in
production each provider builds a fresh `Settings` and a boto3-backed
reader per request. The cost of re-instantiation is negligible
compared to the readability win of having one obvious override point.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from panakoes_query_api.config import Settings
from panakoes_query_api.storage.ingestion import IngestionReader
from panakoes_query_api.storage.sessions import SessionReader
from panakoes_query_api.storage.summaries import SummaryReader


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request."""
    return Settings()


def get_ingestion_reader(
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestionReader:
    """FastAPI dependency: provide a DynamoDB-backed `IngestionReader`."""
    return IngestionReader(
        table_name=settings.ddb_ingestion_table,
        region_name=settings.aws_region,
    )


def get_summary_reader(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SummaryReader:
    """FastAPI dependency: provide a DynamoDB-backed `SummaryReader`."""
    return SummaryReader(
        table_name=settings.ddb_summaries_table,
        region_name=settings.aws_region,
    )


def get_session_reader(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionReader:
    """FastAPI dependency: provide a DynamoDB-backed `SessionReader`."""
    return SessionReader(
        table_name=settings.ddb_sessions_table,
        region_name=settings.aws_region,
    )
