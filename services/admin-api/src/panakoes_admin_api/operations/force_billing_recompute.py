"""Tier 3 lifecycle operation: queue a billing recompute for a tenant.

Publishes an EventBridge event the (future) billing-reconciliation
worker consumes. The recompute itself is async (multi-minute) and
runs out-of-band; the result envelope reports that the recompute was
QUEUED, not completed.

Confirmation template: `RECOMPUTE BILLING <tenant_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from panakoes_admin_api.eventbridge import EventBridgePublisher

logger = structlog.get_logger(__name__)


def make_handler(
    *,
    tenant_id: str,
    publisher: EventBridgePublisher,
    actor_id: str,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for force-billing-recompute")

        queued_at = datetime.now(UTC).isoformat()
        event_id = publisher.put_event(
            source="panakoes.admin-api",
            detail_type="BillingRecomputeRequested",
            detail={
                "tenant_id": tenant_id,
                "queued_at": queued_at,
                "queued_reason": reason,
                "queued_by": actor_id,
            },
        )

        logger.info(
            "tier3_billing_recompute_queued",
            tenant_id=tenant_id,
            event_id=event_id,
            reason=reason,
        )
        return {
            "tenant_id": tenant_id,
            "queued_at": queued_at,
            "queued_reason": reason,
            "eventbridge_event_id": event_id,
        }

    return handler
