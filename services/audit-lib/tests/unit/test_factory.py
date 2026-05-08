"""Tests for the env-driven store factory and global state helpers."""

from __future__ import annotations

import pytest

from panakoes_audit import (
    DynamoDBAuditStore,
    MemoryAuditStore,
    StdoutAuditStore,
    record_event,
    reset_store,
    set_store,
)
from panakoes_audit.config import AuditSettings
from panakoes_audit.factory import _select_store_from_env


@pytest.mark.unit
def test_default_settings_yields_stdout_store() -> None:
    """With no env vars set, the default backend is stdout."""
    settings = AuditSettings()
    assert settings.backend == "stdout"
    store = _select_store_from_env(settings=settings)
    assert isinstance(store, StdoutAuditStore)


@pytest.mark.unit
def test_memory_backend_yields_memory_store() -> None:
    """`AUDIT_BACKEND=memory` resolves to `MemoryAuditStore`."""
    settings = AuditSettings(backend="memory")
    store = _select_store_from_env(settings=settings)
    assert isinstance(store, MemoryAuditStore)


@pytest.mark.unit
def test_dynamodb_backend_yields_dynamodb_store() -> None:
    """`AUDIT_BACKEND=dynamodb` resolves to `DynamoDBAuditStore`."""
    settings = AuditSettings(
        backend="dynamodb", table_name="custom-table", aws_region="us-west-2"
    )
    store = _select_store_from_env(settings=settings)
    assert isinstance(store, DynamoDBAuditStore)
    assert store.table_name == "custom-table"
    assert store.region_name == "us-west-2"


@pytest.mark.unit
def test_select_store_without_settings_argument_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `settings=None`, the factory reads `AUDIT_BACKEND` from env."""
    monkeypatch.setenv("AUDIT_BACKEND", "memory")
    store = _select_store_from_env()
    assert isinstance(store, MemoryAuditStore)


@pytest.mark.unit
async def test_record_event_lazy_initializes_store_from_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without an explicit `set_store`, `record_event` provisions one from env."""
    monkeypatch.setenv("AUDIT_BACKEND", "stdout")
    reset_store()
    await record_event(
        actor_id="u1",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="r1",
        source_service="ingestion",
    )
    captured = capsys.readouterr()
    assert "transcript.created" in captured.out


@pytest.mark.unit
def test_set_store_and_reset_store_round_trip() -> None:
    """`set_store` installs a store, `reset_store` clears it for re-resolution."""
    store = MemoryAuditStore()
    set_store(store)
    # Internal: resetting forgets it; subsequent record_event calls re-resolve.
    reset_store()
    # Setting again should still install cleanly.
    set_store(store)
