"""Streaming-session read endpoints: list and get-one."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_audit import record_event

from panakoes_query_api.auth import AuthenticatedUser, get_current_user
from panakoes_query_api.config import Settings
from panakoes_query_api.models import SessionListResponse, SessionRecord
from panakoes_query_api.routes.dependencies import (
    get_session_reader,
    get_settings,
)
from panakoes_query_api.storage.sessions import SessionReader

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    reader: Annotated[SessionReader, Depends(get_session_reader)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> SessionListResponse:
    """Return the caller's streaming sessions, paginated."""
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
        resource_type="session",
        resource_id="*",
        source_service="query-api",
        details={"count": len(items), "limit": effective_limit},
    )
    return SessionListResponse(items=items, next_cursor=next_cursor)


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    reader: Annotated[SessionReader, Depends(get_session_reader)],
) -> SessionRecord:
    """Return the caller's session by id; 404 otherwise."""
    record = reader.get(user.user_id, session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="query.record_fetched",
        resource_type="session",
        resource_id=session_id,
        source_service="query-api",
    )
    return record
