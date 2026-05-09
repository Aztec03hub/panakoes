"""Settings for the admin-api service.

Tier 3 of the admin dashboard. Hosts the lifecycle operation safety
pattern (typed-confirmation + audit-before-AND-after + idempotency
key + step-up MFA). Phase 0 is a `/health`-only skeleton; Phase 1
lands the safety pattern itself plus the first three lifecycle
operations (terminate session, revoke API credentials, force
password reset).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the admin-api service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "admin-api"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    lifecycle_state_table: str = "panakoes-dev-lifecycle-state"
    audit_log_table: str = "panakoes-dev-audit-log"
    streaming_sessions_table: str = "panakoes-dev-streaming-sessions"
    ingestion_table: str = "panakoes-dev-ingestion"
