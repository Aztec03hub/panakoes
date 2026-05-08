"""Unit tests for `panakoes_event_router.config`."""

from __future__ import annotations

import pytest

from panakoes_event_router.config import load_settings


@pytest.mark.unit
def test_load_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three env vars surface on the returned `Settings`."""
    monkeypatch.setenv("DDB_INGESTION_TABLE", "tbl")
    monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "bus")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    settings = load_settings()
    assert settings.ddb_ingestion_table == "tbl"
    assert settings.eventbridge_bus_name == "bus"
    assert settings.aws_region == "us-west-2"
    # Defaults are stable contracts the handler relies on.
    assert settings.event_source == "panakoes.ingest"
    assert settings.event_detail_type == "AudioUploaded"
    assert settings.metric_namespace == "panakoes/dev/event-router"


@pytest.mark.unit
def test_load_settings_requires_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `DDB_INGESTION_TABLE` is a fail-fast misconfiguration."""
    monkeypatch.setenv("DDB_INGESTION_TABLE", "")
    monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "bus")
    with pytest.raises(RuntimeError, match="DDB_INGESTION_TABLE"):
        load_settings()


@pytest.mark.unit
def test_load_settings_requires_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `EVENTBRIDGE_BUS_NAME` is a fail-fast misconfiguration."""
    monkeypatch.setenv("DDB_INGESTION_TABLE", "tbl")
    monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "")
    with pytest.raises(RuntimeError, match="EVENTBRIDGE_BUS_NAME"):
        load_settings()


@pytest.mark.unit
def test_load_settings_defaults_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing `AWS_REGION` falls back to `us-east-1` (project default)."""
    monkeypatch.setenv("DDB_INGESTION_TABLE", "tbl")
    monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "bus")
    monkeypatch.delenv("AWS_REGION", raising=False)
    settings = load_settings()
    assert settings.aws_region == "us-east-1"
