"""Unit tests for the cost-api `Settings` defaults."""

from __future__ import annotations

import pytest

from panakoes_cost_api.config import Settings


@pytest.mark.unit
def test_settings_loads_defaults() -> None:
    """`Settings()` exposes the documented defaults when no env vars override them."""
    settings = Settings()
    assert settings.service_name == "cost-api"
    assert settings.log_level == "INFO"
    assert settings.aws_region == "us-east-1"
    assert settings.cost_cache_table == "panakoes-dev-cost-cache"
    assert settings.tenant_cost_rollup_table == "panakoes-dev-tenant-cost-rollup"
    assert settings.alert_state_table == "panakoes-dev-alert-state"
    assert settings.audit_log_table == "panakoes-dev-audit-log"


@pytest.mark.unit
def test_settings_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each table name can be overridden by its matching environment variable."""
    monkeypatch.setenv("COST_CACHE_TABLE", "custom-cost-cache")
    monkeypatch.setenv("TENANT_COST_ROLLUP_TABLE", "custom-rollup")
    monkeypatch.setenv("ALERT_STATE_TABLE", "custom-alerts")
    settings = Settings()
    assert settings.cost_cache_table == "custom-cost-cache"
    assert settings.tenant_cost_rollup_table == "custom-rollup"
    assert settings.alert_state_table == "custom-alerts"
