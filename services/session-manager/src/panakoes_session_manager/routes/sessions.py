"""Session lifecycle endpoints.

Endpoints (all JWT-validated, owner-scoped):
- `POST /sessions` create a session record (status=starting, ttl=8h).
- `GET /sessions/{session_id}` fetch one of the caller's sessions.
- `GET /sessions` paginated list of the caller's sessions.
- `PATCH /sessions/{session_id}` update status (state-machine validated)
  and/or attach the GPU instance id.
- `DELETE /sessions/{session_id}` soft-delete (status=errored, ttl=now).

Cross-user reads/writes return 404 (not 403) so we do not leak the
existence of records belonging to other users.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_audit import record_event
from ulid import ULID

from panakoes_session_manager.auth import AuthenticatedUser, get_current_user
from panakoes_session_manager.config import Settings
from panakoes_session_manager.models import (
    CreateSessionRequest,
    SessionListResponse,
    SessionRecord,
    UpdateSessionRequest,
    is_allowed_transition,
)
from panakoes_session_manager.storage.dynamodb import (
    OwnerMismatchError,
    SessionNotFoundError,
    SessionStore,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request.

    Tests override this with a fixture that returns deterministic env
    values; production accepts the cost of re-instantiation.
    """
    return Settings()


def get_session_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionStore:
    """FastAPI dependency: provide a DynamoDB-backed `SessionStore`."""
    return SessionStore(
        table_name=settings.sessions_table_name,
        region_name=settings.aws_region,
    )


def _generate_session_id() -> str:
    """Return a fresh `sess_<ulid>` identifier.

    ULIDs sort lexicographically by creation time (good for cursor
    pagination on a `created_at` range key) and avoid the UUID4-style
    randomness pitfalls in human-facing identifiers.
    """
    return f"sess_{ULID()}"


def _not_found() -> HTTPException:
    """Build the canonical 'session not found' 404."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="session not found",
    )


@router.post(
    "",
    response_model=SessionRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    request: CreateSessionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> SessionRecord:
    """Create a new session in `starting` state."""
    now = datetime.now(UTC)
    session_id = _generate_session_id()
    expires_at = int(now.timestamp()) + settings.session_ttl_seconds

    record = SessionRecord(
        session_id=session_id,
        user_id=user.user_id,
        status="starting",
        language=request.language,
        gpu_instance_id=None,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )
    store.put(record)

    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="session.created",
        resource_type="session",
        resource_id=session_id,
        source_service="session-manager",
        details={"language": request.language},
    )

    return record


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> SessionRecord:
    """Return the caller's session by id (404 cross-user)."""
    record = store.get(user.user_id, session_id)
    if record is None:
        raise _not_found()
    return record


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> SessionListResponse:
    """Return the caller's sessions, paginated newest-first."""
    effective_limit = min(limit, settings.list_max_limit)
    items, next_cursor = store.list_for_user(
        user_id=user.user_id,
        limit=effective_limit,
        cursor=cursor,
    )
    return SessionListResponse(items=items, next_cursor=next_cursor)


@router.patch("/{session_id}", response_model=SessionRecord)
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> SessionRecord:
    """Update a session's status and/or GPU instance id.

    Requires at least one field. Status transitions are validated
    against `ALLOWED_TRANSITIONS`; an illegal transition returns 409.
    """
    if request.status is None and request.gpu_instance_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at least one of 'status' or 'gpu_instance_id' must be provided",
        )

    # Read-before-write so we can validate transitions and emit a rich
    # audit event. The DynamoDB write is still gated on the owner
    # condition expression to close the read-modify-write race.
    current = store.get(user.user_id, session_id)
    if current is None:
        raise _not_found()

    previous_status = current.status
    updates: dict[str, Any] = {"updated_at": datetime.now(UTC).isoformat()}
    if request.status is not None:
        if not is_allowed_transition(previous_status, request.status):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"illegal status transition: {previous_status} -> {request.status}"
                ),
            )
        updates["status"] = request.status
    if request.gpu_instance_id is not None:
        updates["gpu_instance_id"] = request.gpu_instance_id

    try:
        updated = store.update(user.user_id, session_id, updates)
    except (SessionNotFoundError, OwnerMismatchError) as exc:
        # The read above succeeded, so this branch implies a concurrent
        # delete or owner-condition failure; treat both as 404.
        raise _not_found() from exc

    if request.status is not None and request.status != previous_status:
        await record_event(
            actor_id=user.user_id,
            actor_type="user",
            action="session.status_changed",
            resource_type="session",
            resource_id=session_id,
            source_service="session-manager",
            details={
                "from": previous_status,
                "to": request.status,
            },
        )

    return updated


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> None:
    """Soft-delete a session: status=errored, expires_at=now.

    Idempotent for the owner: deleting an already-deleted (or otherwise
    terminal) session still succeeds with a refreshed `expires_at`. The
    DynamoDB TTL sweeper removes the row asynchronously.
    """
    current = store.get(user.user_id, session_id)
    if current is None:
        raise _not_found()

    now = datetime.now(UTC)
    updates: dict[str, Any] = {
        "status": "errored",
        "updated_at": now.isoformat(),
        "expires_at": int(now.timestamp()),
    }
    try:
        store.update(user.user_id, session_id, updates)
    except (SessionNotFoundError, OwnerMismatchError) as exc:
        raise _not_found() from exc

    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="session.deleted",
        resource_type="session",
        resource_id=session_id,
        source_service="session-manager",
        details={"previous_status": current.status},
    )
