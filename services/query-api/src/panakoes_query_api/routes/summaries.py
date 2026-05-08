"""Summary read endpoints: list and get-one."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_audit import record_event

from panakoes_query_api.auth import AuthenticatedUser, get_current_user
from panakoes_query_api.config import Settings
from panakoes_query_api.models import SummaryListResponse, SummaryRecord
from panakoes_query_api.routes.dependencies import (
    get_settings,
    get_summary_reader,
)
from panakoes_query_api.storage.summaries import SummaryReader

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("", response_model=SummaryListResponse)
async def list_summaries(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    reader: Annotated[SummaryReader, Depends(get_summary_reader)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> SummaryListResponse:
    """Return the caller's summary records, paginated."""
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
        resource_type="summary",
        resource_id="*",
        source_service="query-api",
        details={"count": len(items), "limit": effective_limit},
    )
    return SummaryListResponse(items=items, next_cursor=next_cursor)


@router.get("/{transcript_id}", response_model=SummaryRecord)
async def get_summary(
    transcript_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    reader: Annotated[SummaryReader, Depends(get_summary_reader)],
) -> SummaryRecord:
    """Return the caller's summary record by transcript id; 404 otherwise."""
    record = reader.get(user.user_id, transcript_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="summary not found",
        )
    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="query.record_fetched",
        resource_type="summary",
        resource_id=transcript_id,
        source_service="query-api",
    )
    return record
