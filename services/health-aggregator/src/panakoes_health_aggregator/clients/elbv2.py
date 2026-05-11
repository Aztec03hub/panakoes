"""Async wrapper around boto3 `elbv2.describe_target_health`.

Mirrors `clients/ecs.py`'s pattern: small typed projection, blocking
boto3 call offloaded to a worker thread. The aggregator calls this
once per target-group-ARN registered for each service.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol


class Elbv2ClientProtocol(Protocol):
    """The subset of boto3.client('elbv2') we use."""

    def describe_target_health(
        self,
        *,
        TargetGroupArn: str,
    ) -> dict[str, Any]:
        """boto3 elbv2 DescribeTargetHealth."""


class Elbv2Client:
    """Async thin wrapper over `elbv2.describe_target_health`."""

    def __init__(self, client: Elbv2ClientProtocol) -> None:
        self._client = client

    async def all_targets_healthy(self, target_group_arn: str) -> bool:
        """True iff every registered target in the TG is `healthy`.

        Empty target groups (no registered targets at all) return False:
        a TG with nothing attached cannot be considered healthy. boto3
        ClientErrors propagate so the aggregator can roll up to
        "unknown".
        """
        response = await asyncio.to_thread(
            self._client.describe_target_health,
            TargetGroupArn=target_group_arn,
        )
        targets = response.get("TargetHealthDescriptions") or []
        if not targets:
            return False
        return all(
            (t.get("TargetHealth") or {}).get("State") == "healthy"
            for t in targets
        )
