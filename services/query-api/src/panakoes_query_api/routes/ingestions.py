"""Ingestion read endpoints: list and get-one.

Every record returned belongs to the JWT subject. Unknown ids and
cross-user reads collapse to HTTP 404 so we never leak the existence
of records the caller does not own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_audit import record_event

from panakoes_query_api.auth import AuthenticatedUser, get_current_user
from panakoes_query_api.config import Settings
from panakoes_query_api.models import IngestionListResponse, IngestionRecord
from panakoes_query_api.routes.dependencies import (
    get_ingestion_reader,
    get_settings,
)
from panakoes_query_api.storage.ingestion import IngestionReader

router = APIRouter(prefix="/ingestions", tags=["ingestions"])


@router.get("", response_model=IngestionListResponse)
async def list_ingestions(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    reader: Annotated[IngestionReader, Depends(get_ingestion_reader)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> IngestionListResponse:
    """Return the caller's ingestion records, paginated."""
    effective_limit = min(limit, settings.list_max_limit)
    items, next_cursor = reader.list_for_user(
        user_id=user.user_id,
        limit=effective_limit,
        cursor=cursor,
    )
    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="query.records_listed",
        resource_type="ingestion",
        resource_id="*",
        source_service="query-api",
        details={"count": len(items), "limit": effective_limit},
    )
    return IngestionListResponse(items=items, next_cursor=next_cursor)


@router.get("/{ingestion_id}", response_model=IngestionRecord)
async def get_ingestion(
    ingestion_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    reader: Annotated[IngestionReader, Depends(get_ingestion_reader)],
) -> IngestionRecord:
    """Return the caller's ingestion record by id; 404 otherwise."""
    record = reader.get(user.user_id, ingestion_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion not found",
        )
    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="query.record_fetched",
        resource_type="ingestion",
        resource_id=ingestion_id,
        source_service="query-api",
    )
    return record
