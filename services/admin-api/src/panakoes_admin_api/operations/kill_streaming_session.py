"""Tier 3 lifecycle operation: kill a streaming session.

Marks the streaming-sessions row terminated AND sends a tombstone
EventBridge event so the GPU spawner Lambda can decommission the
EC2 instance backing the session. We do NOT call ec2:TerminateInstances
directly; that is the spawner's responsibility.

Confirmation template: `KILL STREAM <session_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

from panakoes_admin_api.eventbridge import EventBridgePublisher

logger = structlog.get_logger(__name__)


class StreamingSessionNotFoundError(Exception):
    """Raised when the target streaming session does not exist."""


def make_handler(
    *,
    sessions_table: Any,
    session_id: str,
    publisher: EventBridgePublisher,
    actor_id: str,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for kill-streaming-session")

        existing = sessions_table.get_item(
            Key={"session_id": session_id}
        ).get("Item")
        if existing is None:
            raise StreamingSessionNotFoundError(
                f"streaming session not found: {session_id}"
            )
        was_active = existing.get("status") in (
            "active",
            "starting",
            "paused",
        )
        killed_at = datetime.now(UTC).isoformat()

        try:
            sessions_table.update_item(
                Key={"session_id": session_id},
                UpdateExpression=(
                    "SET #s = :killed, "
                    "terminated_at = :now, "
                    "termination_reason = :reason, "
                    "termination_source = :source"
                ),
                ConditionExpression="attribute_exists(session_id)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":killed": "errored",
                    ":now": killed_at,
                    ":reason": reason,
                    ":source": "tier3.kill-streaming-session",
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise StreamingSessionNotFoundError(
                    f"streaming session not found: {session_id}"
                ) from exc
            raise

        # Publish the tombstone event. The GPU spawner Lambda subscribes
        # to this detail type and shuts down the underlying EC2 instance.
        event_id = publisher.put_event(
            source="panakoes.admin-api",
            detail_type="StreamingSessionKilled",
            detail={
                "session_id": session_id,
                "killed_at": killed_at,
                "killed_reason": reason,
                "killed_by": actor_id,
            },
        )

        logger.info(
            "tier3_streaming_session_killed",
            session_id=session_id,
            event_id=event_id,
            reason=reason,
        )
        return {
            "session_id": session_id,
            "killed_at": killed_at,
            "killed_reason": reason,
            "eventbridge_event_id": event_id,
            "was_active": was_active,
        }

    return handler
