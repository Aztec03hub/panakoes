"""Tier 3 lifecycle operation: block a tenant.

Sets `blocked_at`, `blocked_reason`, and `blocked_by` on the tenant
record. Subsequent auth checks for any user under this tenant are
expected to reject; the auth-client library is responsible for
honoring the flag (see follow-up note in the run report; this PR
does not modify auth code).

Confirmation template: `BLOCK TENANT <tenant_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class TenantNotFoundError(Exception):
    """Raised when the target tenant does not exist."""


def make_handler(
    *, tenants_table: Any, tenant_id: str, actor_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for block-tenant")

        # Pre-read so we can report previously_blocked accurately.
        existing = tenants_table.get_item(Key={"tenant_id": tenant_id}).get("Item")
        if existing is None:
            raise TenantNotFoundError(f"tenant not found: {tenant_id}")
        previously_blocked = existing.get("blocked_at") is not None
        blocked_at = datetime.now(UTC).isoformat()

        try:
            tenants_table.update_item(
                Key={"tenant_id": tenant_id},
                UpdateExpression=(
                    "SET blocked_at = :now, "
                    "blocked_reason = :reason, "
                    "blocked_by = :actor"
                ),
                ConditionExpression="attribute_exists(tenant_id)",
                ExpressionAttributeValues={
                    ":now": blocked_at,
                    ":reason": reason,
                    ":actor": actor_id,
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise TenantNotFoundError(
                    f"tenant not found: {tenant_id}"
                ) from exc
            raise

        logger.info(
            "tier3_tenant_blocked",
            tenant_id=tenant_id,
            previously_blocked=previously_blocked,
            reason=reason,
        )
        return {
            "tenant_id": tenant_id,
            "blocked_at": blocked_at,
            "blocked_reason": reason,
            "previously_blocked": previously_blocked,
        }

    return handler
