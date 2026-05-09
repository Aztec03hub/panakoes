"""Audit-before-AND-after wrapper for Tier 3 lifecycle operations.

Every dangerous operation writes two audit rows: one `intent` row before
the handler runs, one `outcome` row after (success or failure). The two
rows are joined by the `request_id` field and share the same
`tier3_action` so the Tier 3.3 audit-log read view (built on the
Tier3ActionIndex GSI on `panakoes-dev-audit-log`) returns both.

Why two rows: a partial failure (handler crashes mid-execution, network
flap, container OOM) leaves the `intent` row in place even when the
`outcome` row was never written. That residual `intent`-only row is the
forensic signal that something went wrong; the operator can correlate
to the lifecycle-state row to see what state the operation reached.
Single-row "after only" audit loses this signal entirely.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


logger = structlog.get_logger(__name__)


@asynccontextmanager
async def audit_lifecycle(
    *,
    audit_table: Table,
    actor_id: str,
    tier3_action: str,
    target: dict[str, Any],
    reason: str,
    source_service: str = "admin-api",
) -> AsyncIterator[str]:
    """Wrap a Tier 3 lifecycle operation with audit-before-AND-after writes.

    Yields the `request_id` so the caller can stamp it into the
    `LifecycleResponse.audit_request_id` field. The context manager
    writes the `intent` row on entry and the `outcome` row on exit;
    if the body raises, the `outcome` row records the failure with
    `outcome="failure"` and the exception type. The exception is then
    re-raised so the caller can surface a 5xx.

    Usage:

        async with audit_lifecycle(
            audit_table=table,
            actor_id=claims.sub,
            tier3_action="terminate-session",
            target={"session_id": session_id},
            reason=request.params.reason,
        ) as request_id:
            await terminate_session(session_id)
            # request_id stamped into the response by the caller
    """
    request_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()

    intent_pk = f"AUDIT#{source_service}#{actor_id}"
    intent_sk = f"{timestamp}#{request_id}"

    # Fire-and-forget the intent write. If DynamoDB is down we let the
    # ClientError surface to the caller as a 5xx; we do NOT proceed with
    # the operation under a broken audit pipeline.
    audit_table.put_item(
        Item={
            "pk": intent_pk,
            "sk": intent_sk,
            "actor_id": actor_id,
            "action": f"tier3.{tier3_action}.intent",
            "tier3_action": tier3_action,
            "source_service": source_service,
            "request_id": request_id,
            "timestamp": timestamp,
            "outcome": "pending",
            "target": target,
            "reason": reason,
        }
    )
    logger.info(
        "tier3_audit_intent",
        action=tier3_action,
        request_id=request_id,
        actor=actor_id,
        target=target,
    )

    failure_message: str | None = None
    try:
        yield request_id
    except Exception as exc:
        failure_message = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        outcome = "failure" if failure_message is not None else "success"
        finished_at = datetime.now(UTC).isoformat()
        audit_table.put_item(
            Item={
                "pk": intent_pk,
                "sk": f"{finished_at}#{request_id}#outcome",
                "actor_id": actor_id,
                "action": f"tier3.{tier3_action}.outcome",
                "tier3_action": tier3_action,
                "source_service": source_service,
                "request_id": request_id,
                "timestamp": finished_at,
                "outcome": outcome,
                "target": target,
                "error_message": failure_message,
            }
        )
        logger.info(
            "tier3_audit_outcome",
            action=tier3_action,
            request_id=request_id,
            actor=actor_id,
            outcome=outcome,
            error_message=failure_message,
        )
