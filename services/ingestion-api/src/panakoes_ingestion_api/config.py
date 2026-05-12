"""Settings module for the Ingestion API service.

Environment-driven configuration via `pydantic-settings`. The service
reads the same HS256 secret the Auth service uses to sign tokens via
the `JWT_SECRET` env var (the validator contract; the signer side in
`services/auth` keeps `AUTH_JWT_SECRET`). See `CONTRIBUTING.md` for
the project-wide signer-vs-validator naming rule. Also reads the
names of the DynamoDB table and S3 bucket provisioned by Terraform.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the Ingestion API.

    Override any field at runtime via the matching environment variable
    (e.g. `INGESTION_TABLE_NAME=panakoes-ingestion-dev`). The
    `model_config` block reads from a local `.env` file during
    development without leaking values into production deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "ingestion"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # Auth contract: HS256 shared secret with the Auth service.
    # Env vars: JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE (validator
    # prefix; matches `panakoes_auth_client.from_env()`).
    jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    jwt_issuer: str = "https://auth.panakoes.com"
    jwt_audience: str = "panakoes-api"

    # DynamoDB ingestion table (Terraform-managed).
    ingestion_table_name: str = "panakoes-ingestion"

    # S3 bucket for raw audio uploads (Terraform-managed).
    ingestion_bucket: str = "panakoes-audio-uploads"

    # Pre-signed URL TTL in seconds. 15 minutes is the documented contract.
    presigned_url_ttl_seconds: int = 900

    # Pagination defaults for list endpoint.
    list_default_limit: int = 25
    list_max_limit: int = 100
