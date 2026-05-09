"""Tier 3 lifecycle operation: block all live streaming sessions for a user.

Bulk-terminates every active streaming session belonging to a user,
implementing the "kick this user out NOW" incident-response action.
Unlike `terminate_session` (one specific session), this operation
queries the `UserSessionsIndex` GSI to enumerate the user's sessions
and updates each one in turn.

Partial-failure semantics: if updating session N succeeds and session
N+1 fails, the operation returns a `failed` envelope with
`error_message` set to the first failure. Sessions already terminated
by the time of failure stay terminated (we do NOT roll them back; a
ghost session is not as dangerous as a session that should be
terminated but is not). The audit row's `outcome=failure` records the
partial-completion shape.

Confirmation template: `BLOCK USER <user_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class NoActiveSessionsError(Exception):
    """Raised when the user has no active sessions to block.

    The handler reports this as a successful no-op rather than a
    failure: the post-condition (no active sessions for this user)
    is already true. The result envelope still records the
    affected_count = 0 so the audit trail is unambiguous.
    """


def make_handler(
    *, sessions_table: Any, user_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for block-user-sessions")

        # Enumerate the user's currently-active sessions via the
        # UserSessionsIndex GSI. ProjectionType ALL on the GSI means
        # we can read `status` directly to filter without a follow-up
        # GetItem per session.
        query = sessions_table.query(
            IndexName="UserSessionsIndex",
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
        items = query.get("Items", [])

        active = [
            item
            for item in items
            if item.get("status") in ("active", "starting", "paused")
        ]

        if not active:
            return {
                "user_id": user_id,
                "affected_count": 0,
                "blocked_session_ids": [],
                "skipped_count": len(items),
                "noop": True,
            }

        now = datetime.now(UTC).isoformat()
        blocked_ids: list[str] = []
        first_failure: str | None = None

        for item in active:
            session_id = item["session_id"]
            try:
                sessions_table.update_item(
                    Key={"session_id": session_id},
                    UpdateExpression=(
                        "SET #s = :terminated, "
                        "terminated_at = :now, "
                        "termination_reason = :reason, "
                        "termination_source = :source"
                    ),
                    ConditionExpression="attribute_exists(session_id)",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":terminated": "errored",
                        ":now": now,
                        ":reason": reason,
                        ":source": "tier3.block-user-sessions",
                    },
                )
                blocked_ids.append(session_id)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                first_failure = f"{code} updating {session_id}"
                logger.warning(
                    "tier3_block_user_partial_failure",
                    user_id=user_id,
                    session_id=session_id,
                    error_code=code,
                    blocked_so_far=len(blocked_ids),
                )
                break

        if first_failure is not None:
            # Re-raise so the orchestrator records a failed envelope
            # with the partial-completion details visible.
            raise PartialBlockFailure(
                user_id=user_id,
                blocked_session_ids=blocked_ids,
                first_failure=first_failure,
                total_attempted=len(active),
            )

        logger.info(
            "tier3_user_sessions_blocked",
            user_id=user_id,
            affected_count=len(blocked_ids),
            reason=reason,
        )
        return {
            "user_id": user_id,
            "affected_count": len(blocked_ids),
            "blocked_session_ids": blocked_ids,
            "skipped_count": len(items) - len(active),
            "noop": False,
        }

    return handler


class PartialBlockFailure(Exception):
    """Raised when block-user-sessions hits a session-update failure mid-loop.

    Carries the partial-completion details so the orchestrator's
    failure-envelope path includes them in the audit-log outcome row
    and the LifecycleResponse.error_message field.
    """

    def __init__(
        self,
        *,
        user_id: str,
        blocked_session_ids: list[str],
        first_failure: str,
        total_attempted: int,
    ) -> None:
        self.user_id = user_id
        self.blocked_session_ids = blocked_session_ids
        self.first_failure = first_failure
        self.total_attempted = total_attempted
        super().__init__(
            f"block-user-sessions partial failure for user {user_id}: "
            f"blocked {len(blocked_session_ids)}/{total_attempted}, "
            f"first failure: {first_failure}"
        )
