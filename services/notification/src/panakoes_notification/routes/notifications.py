"""Read-side endpoints: list and fetch a caller's notification history."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from panakoes_notification.auth import AuthenticatedUser, get_current_user
from panakoes_notification.config import Settings
from panakoes_notification.models import (
    NotificationListResponse,
    NotificationRecord,
)
from panakoes_notification.routes.notify import (
    get_notification_store,
    get_settings,
)
from panakoes_notification.storage.dynamodb import NotificationStore

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[NotificationStore, Depends(get_notification_store)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> NotificationListResponse:
    """Return the caller's notification records, paginated newest-first."""
    effective_limit = min(limit, settings.list_max_limit)
    items, next_cursor = store.list_for_user(
        user_id=user.user_id,
        limit=effective_limit,
        cursor=cursor,
    )
    return NotificationListResponse(items=items, next_cursor=next_cursor)


@router.get("/notifications/{notification_id}", response_model=NotificationRecord)
async def get_notification(
    notification_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[NotificationStore, Depends(get_notification_store)],
) -> NotificationRecord:
    """Return a single notification owned by the caller."""
    record = store.get(user.user_id, notification_id)
    if record is None:
        # Cross-user access and not-found collapse to the same 404 so
        # we do not leak the existence of other users' records.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification not found",
        )
    return record
