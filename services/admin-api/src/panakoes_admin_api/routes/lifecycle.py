"""HTTP routes for Tier 3 lifecycle operations.

Each route is a thin shim that:
1. Declares the operation name + expected confirmation template.
2. Builds an operation-specific handler closure.
3. Delegates to `execute_lifecycle_or_failed_envelope` for the
   safety pattern (idempotency + confirmation + audit + step-up).

The pattern keeps every Tier 3 route uniform: adding a new operation
is mostly typing.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from panakoes_auth_client import JwtClaims

from panakoes_admin_api.auth import require_admin_with_step_up
from panakoes_admin_api.dependencies import (
    get_audit_table,
    get_ingestion_table,
    get_lifecycle_state,
    get_streaming_sessions_table,
)
from panakoes_admin_api.lifecycle_state import LifecycleStateStore
from panakoes_admin_api.models import LifecycleRequest, LifecycleResponse
from panakoes_admin_api.operations.block_user_sessions import (
    make_handler as make_block_user_handler,
)
from panakoes_admin_api.operations.force_fail_ingestion import (
    make_handler as make_force_fail_handler,
)
from panakoes_admin_api.operations.terminate_session import (
    make_handler as make_terminate_handler,
)
from panakoes_admin_api.safety import execute_lifecycle_or_failed_envelope

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["lifecycle"])


@router.post(
    "/sessions/{session_id}/terminate",
    response_model=LifecycleResponse,
)
async def terminate_session(
    session_id: str,
    request: LifecycleRequest,
    claims: Annotated[JwtClaims, Depends(require_admin_with_step_up)],
    audit_table: Annotated[Any, Depends(get_audit_table)],
    lifecycle_state: Annotated[LifecycleStateStore, Depends(get_lifecycle_state)],
    sessions_table: Annotated[Any, Depends(get_streaming_sessions_table)],
) -> LifecycleResponse:
    """Terminate a live streaming session.

    Confirmation template: `TERMINATE <session_id>`. The operator
    must type this string verbatim into the dashboard before the
    operation can fire.
    """
    return await execute_lifecycle_or_failed_envelope(
        claims=claims,
        request=request,
        op_name="terminate-session",
        op_handler=make_terminate_handler(
            sessions_table=sessions_table, session_id=session_id
        ),
        expected_confirmation=f"TERMINATE {session_id}",
        target={"session_id": session_id},
        audit_table=audit_table,
        lifecycle_state=lifecycle_state,
    )


@router.post(
    "/ingestions/{ingestion_id}/force-fail",
    response_model=LifecycleResponse,
)
async def force_fail_ingestion(
    ingestion_id: str,
    request: LifecycleRequest,
    claims: Annotated[JwtClaims, Depends(require_admin_with_step_up)],
    audit_table: Annotated[Any, Depends(get_audit_table)],
    lifecycle_state: Annotated[LifecycleStateStore, Depends(get_lifecycle_state)],
    ingestion_table: Annotated[Any, Depends(get_ingestion_table)],
) -> LifecycleResponse:
    """Force-fail a stuck or abusive ingestion record.

    Confirmation template: `FAIL <ingestion_id>`. The operator must
    type this string verbatim before the operation can fire.

    Marks the row in `panakoes-dev-ingestion` as `failed`, attaching
    a failure reason and `failure_source = "tier3.force-fail-ingestion"`
    so post-incident reconciliation can distinguish operator-driven
    failures from pipeline-driven failures.
    """
    return await execute_lifecycle_or_failed_envelope(
        claims=claims,
        request=request,
        op_name="force-fail-ingestion",
        op_handler=make_force_fail_handler(
            ingestion_table=ingestion_table, ingestion_id=ingestion_id
        ),
        expected_confirmation=f"FAIL {ingestion_id}",
        target={"ingestion_id": ingestion_id},
        audit_table=audit_table,
        lifecycle_state=lifecycle_state,
    )


@router.post(
    "/users/{user_id}/block-sessions",
    response_model=LifecycleResponse,
)
async def block_user_sessions(
    user_id: str,
    request: LifecycleRequest,
    claims: Annotated[JwtClaims, Depends(require_admin_with_step_up)],
    audit_table: Annotated[Any, Depends(get_audit_table)],
    lifecycle_state: Annotated[LifecycleStateStore, Depends(get_lifecycle_state)],
    sessions_table: Annotated[Any, Depends(get_streaming_sessions_table)],
) -> LifecycleResponse:
    """Bulk-terminate every active streaming session for a user.

    Confirmation template: `BLOCK USER <user_id>`. The operator must
    type this string verbatim before the operation can fire.

    Implements "kick this user out NOW" incident response: queries the
    UserSessionsIndex GSI for the user's sessions, then issues a
    conditional UpdateItem against each `active` / `starting` /
    `paused` row marking it as `errored` with a `termination_source =
    "tier3.block-user-sessions"` tag. Already-terminated sessions are
    skipped.

    Partial-failure shape: if updating session N succeeds but session
    N+1 fails, the operation surfaces a `failed` envelope with the
    partial-completion count visible in the audit row. Already-blocked
    sessions stay blocked (we do NOT roll back; a ghost session is
    safer than an unkilled one).
    """
    return await execute_lifecycle_or_failed_envelope(
        claims=claims,
        request=request,
        op_name="block-user-sessions",
        op_handler=make_block_user_handler(
            sessions_table=sessions_table, user_id=user_id
        ),
        expected_confirmation=f"BLOCK USER {user_id}",
        target={"user_id": user_id},
        audit_table=audit_table,
        lifecycle_state=lifecycle_state,
    )
