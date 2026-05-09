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
    get_lifecycle_state,
    get_streaming_sessions_table,
)
from panakoes_admin_api.lifecycle_state import LifecycleStateStore
from panakoes_admin_api.models import LifecycleRequest, LifecycleResponse
from panakoes_admin_api.operations.terminate_session import make_handler
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
        op_handler=make_handler(
            sessions_table=sessions_table, session_id=session_id
        ),
        expected_confirmation=f"TERMINATE {session_id}",
        target={"session_id": session_id},
        audit_table=audit_table,
        lifecycle_state=lifecycle_state,
    )
