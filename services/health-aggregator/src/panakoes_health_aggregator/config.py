"""Settings for the health-aggregator service.

Pydantic-settings drives every value off environment variables (or a
local `.env` file during development). The aggregator monitors a fixed
ECS cluster (`panakoes-dev` by default) and resolves the set of
services-to-watch from a static mapping declared in
`SERVICE_REGISTRY` below; if a future iteration needs runtime
overrides, the registry can be hydrated from SSM Parameter Store
without changing the route layer.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the health-aggregator service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "health-aggregator"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # ECS cluster name where every monitored service runs.
    ecs_cluster: str = "panakoes-dev"

    # How recent a log entry must be for the heartbeat heuristic to
    # mark a service healthy. Services with no log in this window get
    # downgraded to "unknown" (unless ECS / TG signals are already
    # unhealthy, in which case unhealthy wins).
    log_heartbeat_window_seconds: int = 300
