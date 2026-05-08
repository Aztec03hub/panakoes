"""Settings module for the summarization service.

Loads configuration from environment variables (or a `.env` file in dev) via
`pydantic-settings`. Every secret value (Anthropic API key, JWT signing
secret) MUST come from the environment; nothing is hardcoded here.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the summarization service.

    Override any field at runtime via the matching environment variable
    (e.g. `JWT_SECRET=...`). Defaults are provided only for non-sensitive
    fields; secrets default to empty strings so misconfiguration surfaces
    as a clean validation error rather than a silent boot.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "summarization"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # JWT (HS256, mirrors the auth service contract per ADR-005)
    jwt_secret: str = ""
    jwt_issuer: str = "panakoes-auth"
    jwt_audience: str = "panakoes"

    # AWS resources for transcripts, summaries, and metadata
    s3_transcripts_bucket: str = "panakoes-dev-transcripts"
    s3_summaries_bucket: str = "panakoes-dev-summaries"
    ddb_summaries_table: str = "panakoes-dev-summaries"

    # Anthropic Claude API
    anthropic_api_key: str = ""
