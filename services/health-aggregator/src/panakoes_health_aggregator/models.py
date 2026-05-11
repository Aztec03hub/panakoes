"""Response shapes returned by the health-aggregator HTTP API.

These intentionally match the existing bundled-mock JSON shape under
`services/admin/static/dashboard/` (snake_case fields, `service`
identifier, `display_name`, `status`, `last_check`, optional
`message`) so the admin SPA can flip its `USE_LIVE_HEALTH_AGGREGATOR`
feature flag without TypeScript changes.

Mirror types: `services/admin/src/lib/types.ts` -> `ServiceHealth`,
`HealthSnapshot`, `ServiceDetail`, `LogEntry`, `ErrorEntry`,
`MetricSnapshot`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HealthStatus = Literal["healthy", "unknown", "unhealthy"]


class ServiceHealth(BaseModel):
    """Rolled-up health entry for a single monitored service.

    The shape matches the static mock at
    `services/admin/static/dashboard/health.json`'s `services[]` entries
    exactly; `message` is omitted when None so healthy rows do not
    carry a null field.
    """

    model_config = ConfigDict(extra="forbid")

    service: str
    display_name: str
    status: HealthStatus
    last_check: datetime
    message: str | None = None


class HealthSnapshot(BaseModel):
    """Top-level snapshot returned by `GET /health`."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    services: list[ServiceHealth] = Field(default_factory=list)


class LogEntry(BaseModel):
    """Recent log line shown on the per-service detail page."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    message: str


class ErrorEntry(BaseModel):
    """Recent recurring-error summary on the detail page."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    message: str
    count: int


class MetricSnapshot(BaseModel):
    """CPU + memory snapshot for the detail page."""

    model_config = ConfigDict(extra="forbid")

    cpu_percent: float
    memory_mb: int
    memory_limit_mb: int


class ServiceDetail(BaseModel):
    """Per-service detail payload returned by `GET /services/{name}`.

    Today the detail endpoint returns the rolled-up health plus
    empty lists / zeroed metrics; future iterations will fill in
    `recent_logs` from CloudWatch Logs Insights and `metrics` from
    ECS Container Insights. The schema is locked now so the SPA
    can render against the live service without further coupling.
    """

    model_config = ConfigDict(extra="forbid")

    service: str
    display_name: str
    health: ServiceHealth
    recent_logs: list[LogEntry] = Field(default_factory=list)
    recent_errors: list[ErrorEntry] = Field(default_factory=list)
    metrics: MetricSnapshot = Field(
        default_factory=lambda: MetricSnapshot(
            cpu_percent=0.0, memory_mb=0, memory_limit_mb=0
        )
    )


class ServiceProbe(BaseModel):
    """Internal: raw probe inputs the aggregator combines into a status.

    Not part of the HTTP surface; lives here so the aggregator and
    its tests share one type.
    """

    model_config = ConfigDict(extra="forbid")

    ecs_running: int
    ecs_desired: int
    ecs_deployments: int
    # Per-target-group target-health rollup: True iff every target in
    # every TG is `healthy`. None means "no target group registered"
    # (services without an HTTP surface) and is treated as a no-op.
    targets_all_healthy: bool | None
    # True iff at least one log event landed within the heartbeat
    # window. None means logs were not checked for this service.
    log_heartbeat: bool | None
    # If any upstream signal exploded (boto3 ClientError), the
    # aggregator records the message here and rolls up to "unknown"
    # rather than failing the entire snapshot.
    error: str | None = None
