"""Tier 3 lifecycle operation: force-fail an ingestion record.

Marks a stuck or abusive ingestion as `failed` so the downstream
pipeline stops processing it. The operator uses this when an upload
is wedged in `pending` / `uploaded` indefinitely or when a tenant
needs a specific upload disowned without waiting for the natural
expiry path.

The handler does the one DynamoDB UpdateItem and returns the new
status. The orchestrator (`safety.execute_lifecycle`) handles the
gates (confirmation, idempotency, audit, step-up).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class IngestionRecordNotFoundError(Exception):
    """Raised when the target ingestion record does not exist."""


def make_handler(
    *, ingestion_table: Any, ingestion_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for force-fail-ingestion")

        # The ingestion table's keys are pk = "USER#" + user_id and
        # sk = "INGESTION#" + ingestion_id. The Tier 3 operator runs
        # against the ingestion_id directly without knowing the
        # owning user; we use the IngestionIdIndex GSI to resolve
        # the row, then UpdateItem against the resolved primary key.
        query = ingestion_table.query(
            IndexName="IngestionIdIndex",
            KeyConditionExpression="ingestion_id = :id",
            ExpressionAttributeValues={":id": ingestion_id},
            Limit=1,
        )
        items = query.get("Items", [])
        if not items:
            raise IngestionRecordNotFoundError(
                f"ingestion record not found: {ingestion_id}"
            )
        target = items[0]
        pk = target["pk"]
        sk = target["sk"]

        try:
            response = ingestion_table.update_item(
                Key={"pk": pk, "sk": sk},
                UpdateExpression=(
                    "SET #s = :failed, "
                    "failed_at = :now, "
                    "failure_reason = :reason, "
                    "failure_source = :source"
                ),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":failed": "failed",
                    ":now": datetime.now(UTC).isoformat(),
                    ":reason": reason,
                    ":source": "tier3.force-fail-ingestion",
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise IngestionRecordNotFoundError(
                    f"ingestion record not found: {ingestion_id}"
                ) from exc
            raise

        new_item = response.get("Attributes", {})
        logger.info(
            "tier3_ingestion_force_failed",
            ingestion_id=ingestion_id,
            reason=reason,
        )
        return {
            "ingestion_id": ingestion_id,
            "status": new_item.get("status", "failed"),
            "failed_at": new_item.get("failed_at"),
        }

    return handler
