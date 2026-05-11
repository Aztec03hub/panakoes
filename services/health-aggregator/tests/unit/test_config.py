"""Unit tests for the `Settings` env-driven config."""

from __future__ import annotations

import pytest

from panakoes_health_aggregator.config import Settings


@pytest.mark.unit
def test_settings_defaults() -> None:
    """Defaults match the cluster + heartbeat window we ship with."""
    s = Settings()
    assert s.service_name == "health-aggregator"
    assert s.ecs_cluster == "panakoes-dev"
    assert s.log_heartbeat_window_seconds == 300


@pytest.mark.unit
def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override defaults."""
    monkeypatch.setenv("ECS_CLUSTER", "panakoes-prod")
    monkeypatch.setenv("LOG_HEARTBEAT_WINDOW_SECONDS", "60")
    s = Settings()
    assert s.ecs_cluster == "panakoes-prod"
    assert s.log_heartbeat_window_seconds == 60
