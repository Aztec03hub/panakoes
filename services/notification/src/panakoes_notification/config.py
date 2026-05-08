"""Settings module for the Notification service.

Environment-driven configuration via `pydantic-settings`. The service
reads the same JWT shared secret the Auth service uses to sign HS256
tokens, plus the SES sender address and DynamoDB table provisioned by
Terraform.

The env-var contract intentionally matches the brief:
- `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`
- `SES_FROM_ADDRESS`
- `DDB_NOTIFICATION_TABLE`
- `AWS_REGION`
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the Notification API.

    Override any field at runtime via the matching environment variable.
    The `model_config` block reads from a local `.env` file during
    development without leaking values into production deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "notification"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # Auth contract: HS256 shared secret with the Auth service.
    # Names match the brief (`JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`).
    jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    jwt_issuer: str = "https://auth.panakoes.com"
    jwt_audience: str = "panakoes-api"

    # SES envelope.
    ses_from_address: str = "no-reply@panakoes.com"

    # DynamoDB notification table (Terraform-managed).
    ddb_notification_table: str = "panakoes-notification"

    # Pagination defaults for list endpoint.
    list_default_limit: int = 25
    list_max_limit: int = 100

    # Webhook retry policy. Three attempts, base 1s, exponential backoff.
    webhook_max_attempts: int = 3
    webhook_base_backoff_seconds: float = 1.0
    webhook_request_timeout_seconds: float = 10.0
