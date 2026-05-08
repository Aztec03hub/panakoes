"""Audit-library configuration loaded from environment variables.

Every consuming service reads these settings on import. We use a
dedicated `AUDIT_` prefix so audit settings cannot collide with the
host service's own env namespace. Defaults are conservative:
`stdout` so that a misconfigured service still emits its events
somewhere observable, rather than dropping them.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AuditBackend = Literal["dynamodb", "stdout", "memory"]


class AuditSettings(BaseSettings):
    """Environment-driven configuration for the audit library."""

    model_config = SettingsConfigDict(
        env_prefix="AUDIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    backend: AuditBackend = "stdout"
    table_name: str = "panakoes-audit-log"
    aws_region: str = "us-east-1"
