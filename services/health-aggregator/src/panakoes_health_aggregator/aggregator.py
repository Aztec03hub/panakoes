"""Health-aggregation orchestration.

Takes a registry entry, fans out three parallel probes (ECS,
target-group health, log heartbeat) via `asyncio.gather`, and rolls
the results into a single `ServiceHealth`. The combination rules
are intentionally simple and explicit:

Status decision matrix (precedence top to bottom; first match wins):

1. Any probe raised an unexpected exception              -> `unknown`
2. ECS service not found                                 -> `unknown`
3. ECS rollout-in-progress (deployments != 1)            -> `unknown`
4. ECS running_count != desired_count                    -> `unhealthy`
5. Target group has unhealthy targets                    -> `unhealthy`
6. Log heartbeat absent within the configured window     -> `unknown`
7. Everything green                                       -> `healthy`

Why "no log heartbeat" is `unknown` and not `unhealthy`: a quiet
service is not the same as a broken service. The dashboard treats
`unknown` as a soft alert (yellow), `unhealthy` as a hard alert
(red). Operators who want a quiet service to alert hard should
either lower the heartbeat window or wire a periodic "I am alive"
log line in the service itself.

The aggregator owns the parallel-fan-out shape; the per-resource
client wrappers stay dumb and synchronous-feeling, which makes them
trivial to mock in unit tests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from panakoes_health_aggregator.clients.cloudwatch import CloudWatchClient
from panakoes_health_aggregator.clients.ecs import EcsClient
from panakoes_health_aggregator.clients.elbv2 import Elbv2Client
from panakoes_health_aggregator.clients.logs import LogsClient
from panakoes_health_aggregator.models import (
    HealthSnapshot,
    HealthStatus,
    ServiceDetail,
    ServiceHealth,
    ServiceProbe,
)
from panakoes_health_aggregator.registry import (
    SERVICE_REGISTRY,
    MonitoredService,
)

logger = structlog.get_logger(__name__)


class HealthAggregator:
    """Composes ECS + ELBv2 + Logs probes into a `HealthSnapshot`."""

    def __init__(
        self,
        ecs: EcsClient,
        elbv2: Elbv2Client,
        logs: LogsClient,
        cloudwatch: CloudWatchClient,
        log_heartbeat_window_seconds: int = 300,
    ) -> None:
        self._ecs = ecs
        self._elbv2 = elbv2
        self._logs = logs
        self._cloudwatch = cloudwatch
        self._heartbeat_window = log_heartbeat_window_seconds

    async def _probe_targets(
        self, entry: MonitoredService
    ) -> bool | None:
        """Return True iff all TGs are healthy; None if entry has no TGs."""
        if not entry.target_group_arns:
            return None
        results = await asyncio.gather(
            *(self._elbv2.all_targets_healthy(arn) for arn in entry.target_group_arns)
        )
        return all(results)

    async def _probe_logs(
        self, entry: MonitoredService
    ) -> bool | None:
        """Return True iff a log event landed inside the window; None if disabled."""
        if not entry.check_logs or entry.log_group is None:
            return None
        return await self._logs.has_recent_event(
            entry.log_group, self._heartbeat_window
        )

    async def probe(self, entry: MonitoredService) -> ServiceProbe:
        """Run all three probes for one service in parallel.

        Exceptions from any individual probe are caught and surfaced
        as a `ServiceProbe.error`; the snapshot continues for the
        other services rather than failing the entire response.
        """
        try:
            ecs_state, targets_healthy, log_heartbeat = await asyncio.gather(
                self._ecs.describe_service(entry.ecs_service),
                self._probe_targets(entry),
                self._probe_logs(entry),
            )
        except Exception as exc:
            logger.warning(
                "health_probe_failed",
                service=entry.service,
                error=str(exc),
            )
            return ServiceProbe(
                ecs_running=0,
                ecs_desired=0,
                ecs_deployments=0,
                targets_all_healthy=None,
                log_heartbeat=None,
                error=str(exc),
            )
        if ecs_state is None:
            # ECS service not found.
            return ServiceProbe(
                ecs_running=0,
                ecs_desired=0,
                ecs_deployments=0,
                targets_all_healthy=targets_healthy,
                log_heartbeat=log_heartbeat,
                error="ecs service not found",
            )
        return ServiceProbe(
            ecs_running=ecs_state.running_count,
            ecs_desired=ecs_state.desired_count,
            ecs_deployments=ecs_state.deployments_count,
            targets_all_healthy=targets_healthy,
            log_heartbeat=log_heartbeat,
            error=None,
        )

    @staticmethod
    def status_from_probe(probe: ServiceProbe) -> tuple[HealthStatus, str | None]:
        """Reduce a probe to (status, optional message) per the matrix above."""
        if probe.error is not None:
            return "unknown", probe.error
        if probe.ecs_deployments != 1:
            return (
                "unknown",
                f"rollout in progress ({probe.ecs_deployments} active deployments)",
            )
        if probe.ecs_running != probe.ecs_desired:
            return (
                "unhealthy",
                f"running={probe.ecs_running} desired={probe.ecs_desired}",
            )
        if probe.targets_all_healthy is False:
            return "unhealthy", "one or more target group targets are unhealthy"
        if probe.log_heartbeat is False:
            return "unknown", "no log events in the heartbeat window"
        return "healthy", None

    async def health_for(self, entry: MonitoredService) -> ServiceHealth:
        """Probe one service and return the rolled-up `ServiceHealth`."""
        probe = await self.probe(entry)
        status, message = self.status_from_probe(probe)
        return ServiceHealth(
            service=entry.service,
            display_name=entry.display_name,
            status=status,
            last_check=datetime.now(UTC),
            message=message,
        )

    async def detail_for(self, entry: MonitoredService) -> ServiceDetail:
        """Probe one service and return the full detail payload with real metrics and logs."""

        async def _no_logs() -> list:  # type: ignore[type-arg]
            return []

        log_group = entry.log_group
        health, recent_logs, recent_errors, metrics = await asyncio.gather(
            self.health_for(entry),
            self._logs.get_recent_events(log_group, window_seconds=3600, limit=20)
            if log_group else _no_logs(),
            self._logs.get_recent_errors(log_group, window_seconds=86400, limit=10)
            if log_group else _no_logs(),
            self._cloudwatch.get_service_metrics(entry.ecs_service),
        )
        return ServiceDetail(
            service=entry.service,
            display_name=entry.display_name,
            health=health,
            recent_logs=recent_logs,
            recent_errors=recent_errors,
            metrics=metrics,
        )

    async def snapshot(
        self, registry: tuple[MonitoredService, ...] = SERVICE_REGISTRY
    ) -> HealthSnapshot:
        """Build a full snapshot by probing every registered service in parallel."""
        services = await asyncio.gather(
            *(self.health_for(entry) for entry in registry)
        )
        return HealthSnapshot(
            generated_at=datetime.now(UTC),
            services=list(services),
        )
