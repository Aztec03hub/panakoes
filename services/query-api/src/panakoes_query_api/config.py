"""Settings module for the Query API service.

Environment-driven configuration via `pydantic-settings`. The service
reads the same `JWT_SECRET` the Auth service uses to sign HS256
tokens, plus the names of the three DynamoDB tables this service
queries (ingestions, summaries, streaming-sessions).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the Query API.

    Override any field at runtime via the matching environment variable
    (e.g. `DDB_INGESTION_TABLE=panakoes-dev-ingestion`). The
    `model_config` block reads from a local `.env` file during
    development without leaking values into production deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "query"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # Auth contract: HS256 shared secret with the Auth service. The
    # variable names match the brief (JWT_SECRET / JWT_ISSUER /
    # JWT_AUDIENCE) rather than the Ingestion API's AUTH_JWT_* prefix
    # so this service can be wired from the same shared-secret env
    # group on its own.
    jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    jwt_issuer: str = "https://auth.panakoes.com"
    jwt_audience: str = "panakoes-api"

    # DynamoDB tables (Terraform-managed under infra/dev/data/).
    ddb_ingestion_table: str = "panakoes-dev-ingestion"
    ddb_summaries_table: str = "panakoes-dev-summaries"
    ddb_sessions_table: str = "panakoes-dev-streaming-sessions"

    # Pagination defaults for list endpoints.
    list_default_limit: int = 25
    list_max_limit: int = 100
