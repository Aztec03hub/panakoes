"""Tests for `StdoutAuditStore`."""

from __future__ import annotations

import json

import pytest

from panakoes_audit import AuditEvent, AuditStore, StdoutAuditStore


@pytest.mark.unit
async def test_stdout_store_emits_single_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    """`record` prints exactly one parseable JSON line per event."""
    store = StdoutAuditStore()
    event = AuditEvent(
        actor_id="u1",
        actor_type="user",
        action="transcript.created",
        resource_type="transcript",
        resource_id="r1",
        source_service="ingestion",
    )
    await store.record(event)
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["actor_id"] == "u1"
    assert payload["action"] == "transcript.created"
    assert payload["request_id"] == event.request_id


@pytest.mark.unit
async def test_stdout_store_satisfies_audit_store_protocol() -> None:
    """Structural typing: `StdoutAuditStore` is an `AuditStore`."""
    store = StdoutAuditStore()
    assert isinstance(store, AuditStore)
