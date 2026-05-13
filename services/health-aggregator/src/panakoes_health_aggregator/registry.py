"""Service registry: the static set of services the aggregator monitors.

The dashboard's Tier 1 health view shows a fixed list (matching the
bundled mock in `services/admin/static/dashboard/health.json`). Each
entry maps the SPA-facing service identifier and display name to the
underlying ECS service name and any registered ELBv2 target group
ARNs and CloudWatch log group name.

Keep this list in lockstep with the static mock until the live
service replaces the mock entirely; the order here drives the
order surfaced to the dashboard.

Why a hardcoded registry: at v0.1 the set of services is small (~10)
and changes on the same cadence as Terraform. Discovery (listing ECS
services and pattern-matching their tags) is a follow-up; the YAGNI
cost of dynamic discovery today is higher than the maintenance cost
of one Python list.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MonitoredService:
    """One row in the static registry."""

    service: str
    display_name: str
    ecs_service: str
    target_group_arns: tuple[str, ...] = field(default_factory=tuple)
    log_group: str | None = None
    check_logs: bool = True


# All planned services. Services without a live ECS deployment report
# "unknown / ecs service not found" -- that is intentional signal, not
# noise. Add entries here as new services are designed; they will
# automatically flip to "healthy" once the ECS service ships.
SERVICE_REGISTRY: tuple[MonitoredService, ...] = (
    MonitoredService(
        service="auth",
        display_name="Auth",
        ecs_service="panakoes-dev-auth",
        log_group="/panakoes/dev/auth",
    ),
    MonitoredService(
        service="admin-api",
        display_name="Admin API",
        ecs_service="panakoes-dev-admin-api",
        log_group="/panakoes/dev/admin-api",
    ),
    MonitoredService(
        service="cost-api",
        display_name="Cost API",
        ecs_service="panakoes-dev-cost-api",
        log_group="/panakoes/dev/cost-api",
    ),
    MonitoredService(
        service="ingestion-api",
        display_name="Ingestion API",
        ecs_service="panakoes-dev-ingestion-api",
        log_group="/panakoes/dev/ingestion-api",
    ),
    MonitoredService(
        service="summarization",
        display_name="Summarization",
        ecs_service="panakoes-dev-summarization",
        log_group="/panakoes/dev/summarization",
    ),
    MonitoredService(
        service="notification",
        display_name="Notification",
        ecs_service="panakoes-dev-notification",
        log_group="/panakoes/dev/notification",
    ),
    MonitoredService(
        service="query-api",
        display_name="Query API",
        ecs_service="panakoes-dev-query-api",
        log_group="/panakoes/dev/query-api",
    ),
    MonitoredService(
        service="session-manager",
        display_name="Session Manager",
        ecs_service="panakoes-dev-session-manager",
        log_group="/panakoes/dev/session-manager",
    ),
    MonitoredService(
        service="gpu-spawner",
        display_name="GPU Spawner",
        ecs_service="panakoes-dev-gpu-spawner",
        log_group="/panakoes/dev/gpu-spawner",
    ),
    MonitoredService(
        service="transcriber-batch",
        display_name="Transcriber (batch)",
        ecs_service="panakoes-dev-transcriber-batch",
        log_group="/panakoes/dev/transcriber-batch",
    ),
    MonitoredService(
        service="transcriber-stream",
        display_name="Transcriber (stream)",
        ecs_service="panakoes-dev-transcriber-stream",
        log_group="/panakoes/dev/transcriber-stream",
    ),
    MonitoredService(
        service="event-router",
        display_name="Event Router",
        ecs_service="panakoes-dev-event-router",
        log_group="/panakoes/dev/event-router",
    ),
    MonitoredService(
        service="billing",
        display_name="Billing",
        ecs_service="panakoes-dev-billing",
        log_group="/panakoes/dev/billing",
    ),
)


def get_service(name: str) -> MonitoredService | None:
    """Look up a registry entry by SPA-facing service identifier.

    Returns None for unknown names so the route layer can map to 404
    without raising.
    """
    for entry in SERVICE_REGISTRY:
        if entry.service == name:
            return entry
    return None
