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
    # Phase 2 lifecycle ops. The tenants + api-keys tables do not yet
    # exist in `infra/dev/data/`; an operator follow-up Terraform PR
    # provisions them. Until then, these settings name the tables the
    # service expects to find.
    tenants_table: str = "panakoes-dev-tenants"
    api_keys_table: str = "panakoes-dev-api-keys"
    # AWS Batch + EventBridge wiring for the kill-batch-job and
    # streaming/billing event-driven ops respectively. The bus name
    # matches `local.name_prefix` in `infra/dev/events/`.
    events_bus_name: str = "panakoes-dev"

    # Toggle Swagger UI (`/docs`), ReDoc (`/redoc`), and the live
    # OpenAPI JSON (`/openapi.json`) on the running service. Default
    # on for dev; production deploys flip this off via env var so the
    # interactive docs surface never reaches the public internet.
    # The checked-in `services/admin-api/openapi.json` artifact is the
    # canonical source for client codegen regardless of this flag.
    enable_openapi_docs: bool = True
