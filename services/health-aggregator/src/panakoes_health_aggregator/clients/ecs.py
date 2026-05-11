"""Async wrapper around boto3 `ecs.describe_services`.

boto3 is synchronous; we offload the blocking call to a thread via
`asyncio.to_thread` so the aggregator can fan multiple per-service
probes out in parallel via `asyncio.gather` without blocking the
event loop. The wrapper exposes a tiny typed surface (running /
desired / deployments) rather than the raw boto3 response.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class EcsServiceState:
    """Minimal projection of an ECS DescribeServices result."""

    running_count: int
    desired_count: int
    deployments_count: int


class EcsClientProtocol(Protocol):
    """The subset of boto3.client('ecs') we use; lets tests inject fakes."""

    def describe_services(
        self,
        *,
        cluster: str,
        services: list[str],
    ) -> dict[str, Any]:
        """boto3 ecs DescribeServices."""


class EcsClient:
    """Async thin wrapper over `ecs.describe_services`."""

    def __init__(self, client: EcsClientProtocol, cluster: str) -> None:
        self._client = client
        self._cluster = cluster

    async def describe_service(self, ecs_service_name: str) -> EcsServiceState | None:
        """Return the ECS service's running / desired / deployment counts.

        Returns None when the service is not present (a common pre-deploy
        state in dev). Any boto3 ClientError propagates; the aggregator
        catches it and rolls up to an "unknown" probe.
        """
        response = await asyncio.to_thread(
            self._client.describe_services,
            cluster=self._cluster,
            services=[ecs_service_name],
        )
        services = response.get("services") or []
        if not services:
            return None
        svc = services[0]
        return EcsServiceState(
            running_count=int(svc.get("runningCount", 0)),
            desired_count=int(svc.get("desiredCount", 0)),
            deployments_count=len(svc.get("deployments") or []),
        )
