"""Unit tests for the admin-api `Settings` defaults."""

from __future__ import annotations

import pytest

from panakoes_admin_api.config import Settings


@pytest.mark.unit
def test_settings_loads_defaults() -> None:
    """`Settings()` exposes the documented defaults when no env vars override them."""
    settings = Settings()
    assert settings.service_name == "admin-api"
    assert settings.log_level == "INFO"
    assert settings.aws_region == "us-east-1"
    assert settings.lifecycle_state_table == "panakoes-dev-lifecycle-state"
    assert settings.audit_log_table == "panakoes-dev-audit-log"
    assert settings.streaming_sessions_table == "panakoes-dev-streaming-sessions"
    assert settings.ingestion_table == "panakoes-dev-ingestion"


@pytest.mark.unit
def test_settings_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each table name can be overridden by its matching environment variable."""
    monkeypatch.setenv("LIFECYCLE_STATE_TABLE", "custom-lifecycle")
    monkeypatch.setenv("AUDIT_LOG_TABLE", "custom-audit")
    settings = Settings()
    assert settings.lifecycle_state_table == "custom-lifecycle"
    assert settings.audit_log_table == "custom-audit"
