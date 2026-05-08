"""Settings module for the Session Manager service.

Environment-driven configuration via `pydantic-settings`. The service
reads the same `AUTH_JWT_SECRET` the Auth service uses to sign HS256
tokens, plus the name of the streaming-sessions DynamoDB table
provisioned by Terraform.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the Session Manager.

    Override any field at runtime via the matching environment variable
    (e.g. `SESSIONS_TABLE_NAME=panakoes-streaming-sessions-dev`). The
    `model_config` block reads from a local `.env` file during
    development without leaking values into production deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "session-manager"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # Auth contract: HS256 shared secret with the Auth service.
    auth_jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    auth_jwt_issuer: str = "https://auth.panakoes.com"
    auth_jwt_audience: str = "panakoes-api"

    # DynamoDB streaming-sessions table (Terraform-managed).
    sessions_table_name: str = "panakoes-streaming-sessions"

    # Session lifetime. The DynamoDB TTL attribute is set to
    # `now + session_ttl_seconds` at creation; rows are auto-deleted
    # within ~48 hours of that timestamp.
    session_ttl_seconds: int = 8 * 60 * 60  # 8 hours

    # Pagination defaults for list endpoint.
    list_default_limit: int = 25
    list_max_limit: int = 100
