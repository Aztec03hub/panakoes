"""Tier 3 lifecycle orchestrator.

`execute_lifecycle` ties the four safety layers together (typed
confirmation + idempotency-by-key + audit-before-AND-after + step-up
MFA) and runs an operation handler under their protection. Every
Tier 3 lifecycle route delegates to this function; the route's own
body is reduced to declaring the typed param model, the typed result
shape, and the operation handler.

Response semantics (intentional design choice, codified in ADR-032):

- A pre-flight gate failure (wrong confirmation, missing idempotency
  key, auth gate) raises an `HTTPException` and the route returns the
  matching 4xx. The lifecycle protocol never starts; nothing is
  audited; nothing is recorded in `lifecycle_state`.
- Once the pre-flight gates pass, the protocol *always* completes:
  the response is HTTP 200 with a `LifecycleResponse` envelope. The
  operation outcome (success / failure) is encoded in
  `envelope.status`; the response body, not the HTTP code, is the
  authoritative outcome record.

This split lets the dashboard distinguish "your request was rejected"
(retry by fixing the request) from "your request ran but the
operation failed" (forensic outcome, idempotency key reusable as a
cached result on repeat).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import HTTPException, status
from panakoes_auth_client import JwtClaims

from panakoes_admin_api.audit import audit_lifecycle
from panakoes_admin_api.lifecycle_state import LifecycleStateStore
from panakoes_admin_api.models import (
    LifecycleRequest,
    LifecycleResponse,
    LifecycleStatus,
)

logger = structlog.get_logger(__name__)


OperationHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
"""Async function `params -> result_dict` invoked once the gates pass."""


async def execute_lifecycle(
    *,
    claims: JwtClaims,
    request: LifecycleRequest,
    op_name: str,
    op_handler: OperationHandler,
    expected_confirmation: str,
    target: dict[str, Any],
    audit_table: Any,
    lifecycle_state: LifecycleStateStore,
) -> LifecycleResponse:
    """Run a Tier 3 lifecycle operation under the safety pattern."""
    cached = lifecycle_state.get(request.idempotency_key)
    if cached is not None:
        if cached.status == LifecycleStatus.PENDING:
            # A prior attempt is still in progress (or crashed before
            # update_terminal). The dashboard should re-poll. We do
            # NOT re-run the handler under the same idempotency_key
            # because the original attempt may still be writing to
            # downstream systems.
            return cached
        # Terminal status: the operation already completed at-most-once.
        # Return the cached envelope verbatim.
        return cached

    if request.confirmation != expected_confirmation:
        logger.info(
            "tier3_confirmation_mismatch",
            op=op_name,
            actor=claims.sub,
            expected=expected_confirmation,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"confirmation mismatch: expected '{expected_confirmation}'"
            ),
        )

    reason = str(request.params.get("reason", ""))

    async with audit_lifecycle(
        audit_table=audit_table,
        actor_id=claims.sub,
        tier3_action=op_name,
        target=target,
        reason=reason,
    ) as audit_request_id:
        seed = lifecycle_state.put_pending(
            idempotency_key=request.idempotency_key,
            audit_request_id=audit_request_id,
        )
        try:
            result = await op_handler(request.params)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "tier3_handler_raised",
                op=op_name,
                actor=claims.sub,
                error=error_message,
            )
            envelope = lifecycle_state.update_terminal(
                idempotency_key=request.idempotency_key,
                status=LifecycleStatus.FAILED,
                error_message=error_message,
                audit_request_id=audit_request_id,
                started_at=seed.started_at,
            )
            # Re-raise so audit_lifecycle records the failure outcome
            # row. The orchestrator itself catches OUTSIDE the
            # context manager and re-emits as 200-with-failed-status.
            raise _LifecycleHandlerFailed(envelope) from exc

        envelope = lifecycle_state.update_terminal(
            idempotency_key=request.idempotency_key,
            status=LifecycleStatus.SUCCEEDED,
            result=result,
            audit_request_id=audit_request_id,
            started_at=seed.started_at,
        )

    return envelope


class _LifecycleHandlerFailed(Exception):
    """Internal sentinel: handler raised, envelope already finalized.

    Re-raised inside the audit context manager so the failure outcome
    row gets written. Caught immediately outside the context manager
    by the route shim that returns the failed envelope as HTTP 200.
    """

    def __init__(self, envelope: LifecycleResponse) -> None:
        super().__init__(envelope.error_message or "lifecycle handler failed")
        self.envelope = envelope


async def execute_lifecycle_or_failed_envelope(
    *,
    claims: JwtClaims,
    request: LifecycleRequest,
    op_name: str,
    op_handler: OperationHandler,
    expected_confirmation: str,
    target: dict[str, Any],
    audit_table: Any,
    lifecycle_state: LifecycleStateStore,
) -> LifecycleResponse:
    """Wrapper that converts handler-raised failures into a finalized envelope.

    This is the function routes call. Pre-flight gate failures
    (`HTTPException`) propagate as 4xx; handler failures are caught
    here and returned as 200-with-failed-status, per ADR-032.
    """
    try:
        return await execute_lifecycle(
            claims=claims,
            request=request,
            op_name=op_name,
            op_handler=op_handler,
            expected_confirmation=expected_confirmation,
            target=target,
            audit_table=audit_table,
            lifecycle_state=lifecycle_state,
        )
    except _LifecycleHandlerFailed as exc:
        return exc.envelope
