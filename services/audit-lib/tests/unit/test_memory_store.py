"""Tests for `MemoryAuditStore` and the `record_event` helper."""

from __future__ import annotations

import pytest

from panakoes_audit import (
    AuditEvent,
    AuditStore,
    MemoryAuditStore,
    record_event,
    set_store,
)


@pytest.mark.unit
async def test_memory_store_records_event() -> None:
    """`record` appends events in order; `events` reads them back."""
    store = MemoryAuditStore()
    event = AuditEvent(
        actor_id="u1",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="r1",
        source_service="ingestion",
    )
    await store.record(event)
    assert store.events == [event]


@pytest.mark.unit
async def test_memory_store_clear_removes_all_events() -> None:
    """`clear()` empties the recorded list."""
    store = MemoryAuditStore()
    event = AuditEvent(
        actor_id="u1",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="r1",
        source_service="ingestion",
    )
    await store.record(event)
    store.clear()
    assert store.events == []


@pytest.mark.unit
async def test_memory_store_satisfies_audit_store_protocol() -> None:
    """`MemoryAuditStore` is a structural subtype of `AuditStore`."""
    store = MemoryAuditStore()
    assert isinstance(store, AuditStore)


@pytest.mark.unit
async def test_record_event_uses_injected_memory_store() -> None:
    """`record_event` writes through whatever store `set_store` installed."""
    store = MemoryAuditStore()
    set_store(store)
    event = await record_event(
        actor_id="user_abc",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="trnscr_xyz",
        source_service="ingestion",
        request_id="req_def",
        details={"audio_duration_seconds": 245},
        source_ip="203.0.113.42",
    )
    assert store.events == [event]
    recorded = store.events[0]
    assert recorded.request_id == "req_def"
    assert recorded.details == {"audio_duration_seconds": 245}
    assert recorded.source_ip == "203.0.113.42"


@pytest.mark.unit
async def test_record_event_autogenerates_request_id_when_omitted() -> None:
    """Omitting `request_id` causes `AuditEvent` to mint a UUID4."""
    store = MemoryAuditStore()
    set_store(store)
    event = await record_event(
        actor_id="user_abc",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="trnscr_xyz",
        source_service="ingestion",
    )
    assert event.request_id  # truthy
    assert event.details == {}
    assert event.source_ip is None


@pytest.mark.unit
async def test_record_event_returns_validated_event() -> None:
    """The returned event reflects validated values, including details default."""
    store = MemoryAuditStore()
    set_store(store)
    event = await record_event(
        actor_id="user_abc",
        actor_type="service",
        action="summary.generated",
        resource_type="summary",
        resource_id="sm_xyz",
        source_service="summarizer",
    )
    assert event.actor_type == "service"
    assert event.action == "summary.generated"
