"""Settings for the cost-api service.

Pydantic-settings drives every value off environment variables (or a
local `.env` file during development). The Tier 2 implementation plan
(`docs/design/tier-2-3-implementation-plan.md`) calls out the four
DynamoDB tables this service reads and writes; their names are
overridable so test rigs and per-environment Terraform can plug in
distinct table names without code changes.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the cost-api service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "cost-api"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    cost_cache_table: str = "panakoes-dev-cost-cache"
    tenant_cost_rollup_table: str = "panakoes-dev-tenant-cost-rollup"
    alert_state_table: str = "panakoes-dev-alert-state"
    audit_log_table: str = "panakoes-dev-audit-log"
