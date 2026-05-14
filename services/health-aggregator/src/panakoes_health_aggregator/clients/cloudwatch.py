"""Async wrapper around boto3 CloudWatch GetMetricData for Container Insights."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from panakoes_health_aggregator.models import MetricSnapshot


class CloudWatchClientProtocol(Protocol):
    """The subset of boto3.client('cloudwatch') we use."""

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        """boto3 cloudwatch GetMetricData."""
        ...


class CloudWatchClient:
    """Fetches ECS Container Insights CPU/memory metrics for a service."""

    def __init__(self, client: CloudWatchClientProtocol, cluster: str) -> None:
        self._client = client
        self._cluster = cluster

    async def get_service_metrics(self, ecs_service: str) -> MetricSnapshot:
        """Return the most recent CPU% and memory (MB) for an ECS service.

        Returns zeroed snapshot if Container Insights has no data yet
        (e.g. service just started or Insights was recently enabled).
        """
        now = datetime.now(UTC)
        start = now - timedelta(minutes=10)

        def _dimensions(metric: str) -> dict[str, Any]:
            return {
                "Id": metric.lower(),
                "MetricStat": {
                    "Metric": {
                        "Namespace": "ECS/ContainerInsights",
                        "MetricName": metric,
                        "Dimensions": [
                            {"Name": "ClusterName", "Value": self._cluster},
                            {"Name": "ServiceName", "Value": ecs_service},
                        ],
                    },
                    "Period": 60,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }

        try:
            response = await asyncio.to_thread(
                self._client.get_metric_data,
                MetricDataQueries=[
                    _dimensions("CpuUtilized"),
                    _dimensions("CpuReserved"),
                    _dimensions("MemoryUtilized"),
                    _dimensions("MemoryReserved"),
                ],
                StartTime=start,
                EndTime=now,
            )
        except Exception:
            return MetricSnapshot(cpu_percent=0.0, memory_mb=0, memory_limit_mb=0)

        results = {r["Id"]: r.get("Values", []) for r in response.get("MetricDataResults", [])}

        cpu_used = results.get("cpuutilized", [])
        cpu_reserved = results.get("cpureserved", [])
        mem_used = results.get("memoryutilized", [])
        mem_reserved = results.get("memoryreserved", [])

        cpu_val = cpu_used[0] if cpu_used else 0.0
        cpu_cap = cpu_reserved[0] if cpu_reserved else 0.0
        mem_val = int(mem_used[0]) if mem_used else 0
        mem_limit = int(mem_reserved[0]) if mem_reserved else 0

        cpu_pct = round((cpu_val / cpu_cap * 100), 1) if cpu_cap else 0.0

        return MetricSnapshot(
            cpu_percent=cpu_pct,
            memory_mb=mem_val,
            memory_limit_mb=mem_limit,
        )
