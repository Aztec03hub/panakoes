"""Tier 3 lifecycle operation: terminate a live streaming session.

Updates the row in `panakoes-dev-streaming-sessions` to mark the
session as terminated. The Session Manager service polls this table
and disconnects clients on the next reconciliation tick.

The handler is intentionally thin: it does the one DynamoDB update
and returns a result envelope. The orchestrator
(`safety.execute_lifecycle`) handles confirmation, idempotency,
audit, and step-up gating.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class SessionNotFoundError(Exception):
    """Raised when the target session does not exist in the table."""


def make_handler(
    *, sessions_table: Any, session_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke.

    The handler captures the table + session_id by closure so the
    orchestrator's `OperationHandler` callable signature
    (`params -> result_dict`) remains operation-agnostic.
    """

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for terminate-session")

        try:
            response = sessions_table.update_item(
                Key={"session_id": session_id},
                UpdateExpression=(
                    "SET #s = :terminated, "
                    "terminated_at = :now, "
                    "termination_reason = :reason"
                ),
                ConditionExpression="attribute_exists(session_id)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":terminated": "errored",
                    ":now": datetime.now(UTC).isoformat(),
                    ":reason": reason,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise SessionNotFoundError(
                    f"session not found: {session_id}"
                ) from exc
            raise

        new_item = response.get("Attributes", {})
        logger.info(
            "tier3_session_terminated",
            session_id=session_id,
            reason=reason,
        )
        return {
            "session_id": session_id,
            "status": new_item.get("status", "errored"),
            "terminated_at": new_item.get("terminated_at"),
        }

    return handler
