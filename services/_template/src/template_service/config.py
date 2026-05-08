"""Settings module for the template service.

Uses `pydantic-settings` so configuration values are loaded from environment
variables (or a `.env` file when present), with type-checked defaults. Each
new service copies this pattern and adds its own service-specific fields.

Example:
    >>> from template_service.config import Settings
    >>> settings = Settings()
    >>> settings.service_name
    'template'
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the service.

    Override any field at runtime via the matching environment variable
    (e.g. `SERVICE_NAME=ingestion`). The `model_config` block allows the
    process to read from a local `.env` file during development without
    leaking values into production deploys, where env vars dominate.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "template"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"
