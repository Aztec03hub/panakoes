"""DynamoDB wrapper for the `panakoes-dev-lifecycle-state` table.

Backs the idempotency-by-key portion of the Tier 3 safety pattern.
Every dangerous operation writes a `pending` row keyed on the caller's
`idempotency_key` before the handler runs. On completion (success or
failure), the row is upgraded to a terminal status with the result or
error envelope.

Repeated submissions of the same `idempotency_key` collapse to a single
side-effect: the second submission's pre-flight `get` returns the
existing row, and the route layer returns the cached response without
running the handler again. The caller therefore gets at-most-once
semantics for free.

TTL on `expires_at` is a 24-hour safety net; the `panakoes-dev-lifecycle-state`
table sweeps the row out asynchronously after that. Anything older than
24 hours is treated as a fresh operation; the audit log retains the
permanent record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from panakoes_admin_api.models import LifecycleResponse, LifecycleStatus

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


logger = structlog.get_logger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class LifecycleStateStore:
    """DynamoDB-backed idempotency store for Tier 3 operations."""

    def __init__(self, table: Table) -> None:
        self._table = table

    def get(self, idempotency_key: str) -> LifecycleResponse | None:
        """Return the cached response for `idempotency_key`, or None if absent."""
        response = self._table.get_item(Key={"idempotency_key": idempotency_key})
        item = response.get("Item")
        if item is None:
            return None
        payload = item.get("response_envelope")
        if payload is None or not isinstance(payload, str):
            logger.warning(
                "lifecycle_state_corrupt_row",
                idempotency_key=idempotency_key,
            )
            return None
        return LifecycleResponse.model_validate_json(payload)

    def put_pending(
        self,
        *,
        idempotency_key: str,
        audit_request_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> LifecycleResponse:
        """Record a fresh pending operation. Returns the seed envelope."""
        now = datetime.now(UTC)
        envelope = LifecycleResponse(
            idempotency_key=idempotency_key,
            status=LifecycleStatus.PENDING,
            audit_request_id=audit_request_id,
            started_at=now,
        )
        expires_at = int(now.timestamp()) + ttl_seconds
        self._table.put_item(
            Item={
                "idempotency_key": idempotency_key,
                "response_envelope": envelope.model_dump_json(),
                "expires_at": expires_at,
                "audit_request_id": audit_request_id,
                "status": envelope.status.value,
            }
        )
        return envelope

    def update_terminal(
        self,
        *,
        idempotency_key: str,
        status: LifecycleStatus,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        audit_request_id: str,
        started_at: datetime,
    ) -> LifecycleResponse:
        """Promote a pending row to a terminal status. Returns the final envelope."""
        if status not in (
            LifecycleStatus.SUCCEEDED,
            LifecycleStatus.FAILED,
            LifecycleStatus.CANCELLED,
        ):
            raise ValueError(
                f"update_terminal requires a terminal status, got {status}"
            )
        finished_at = datetime.now(UTC)
        envelope = LifecycleResponse(
            idempotency_key=idempotency_key,
            status=status,
            result=result,
            error_message=error_message,
            audit_request_id=audit_request_id,
            started_at=started_at,
            finished_at=finished_at,
        )
        # Preserve the existing expires_at by issuing an UpdateItem
        # rather than a PutItem; otherwise we'd reset the TTL window
        # when finalizing the row, which leaks more retention than we
        # intend.
        self._table.update_item(
            Key={"idempotency_key": idempotency_key},
            UpdateExpression=(
                "SET response_envelope = :env, #s = :st"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":env": envelope.model_dump_json(),
                ":st": envelope.status.value,
            },
        )
        return envelope
