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

    # Cache TTLs. CE GetCostAndUsage and GetCostForecast both refresh
    # at most once per day upstream, so caching for hours is safe and
    # protects the 29s API Gateway integration timeout from CE hangs.
    # PR #222 (cost-api 503 hang on /by-service) traced to back-to-back
    # CE calls bypassing a too-short cache TTL.
    cost_cache_ttl_seconds: int = 3600  # 1 hour
    forecast_cache_ttl_seconds: int = 6 * 3600  # 6 hours
    anomaly_cache_ttl_seconds: int = 1800  # 30 minutes

    # boto3 ce client timeouts. API Gateway's integration timeout is
    # 29s; surfacing CE hangs fast at 15s read / 5s connect lets the
    # route map them to a clean 503 instead of leaving the gateway to
    # time us out with a generic 503. `adaptive` retry mode adds
    # client-side throttle awareness on top of CE's server throttle.
    ce_connect_timeout_seconds: int = 5
    ce_read_timeout_seconds: int = 15
    ce_max_retry_attempts: int = 2
