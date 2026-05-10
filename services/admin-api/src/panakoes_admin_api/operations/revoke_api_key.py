"""Tier 3 lifecycle operation: revoke an API key.

Marks the row in the api-keys table as revoked. Future authenticator
checks reject the key.

Confirmation template: `REVOKE KEY <api_key_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class ApiKeyNotFoundError(Exception):
    """Raised when the target API key does not exist."""


def make_handler(
    *, api_keys_table: Any, api_key_id: str, actor_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for revoke-api-key")

        existing = api_keys_table.get_item(Key={"api_key_id": api_key_id}).get("Item")
        if existing is None:
            raise ApiKeyNotFoundError(f"api key not found: {api_key_id}")
        was_active = existing.get("status", "active") == "active" and (
            existing.get("revoked_at") is None
        )
        revoked_at = datetime.now(UTC).isoformat()

        try:
            api_keys_table.update_item(
                Key={"api_key_id": api_key_id},
                UpdateExpression=(
                    "SET #s = :revoked, "
                    "revoked_at = :now, "
                    "revoked_reason = :reason, "
                    "revoked_by = :actor"
                ),
                ConditionExpression="attribute_exists(api_key_id)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":revoked": "revoked",
                    ":now": revoked_at,
                    ":reason": reason,
                    ":actor": actor_id,
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise ApiKeyNotFoundError(
                    f"api key not found: {api_key_id}"
                ) from exc
            raise

        logger.info(
            "tier3_api_key_revoked",
            api_key_id=api_key_id,
            was_active=was_active,
            reason=reason,
        )
        return {
            "api_key_id": api_key_id,
            "revoked_at": revoked_at,
            "revoked_reason": reason,
            "was_active": was_active,
        }

    return handler
