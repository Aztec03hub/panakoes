"""Integration tests for `DynamoDBAuditStore` via moto."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from panakoes_audit import AuditEvent, AuditStore, DynamoDBAuditStore


@pytest.mark.integration
async def test_dynamodb_store_writes_event_with_expected_keys(
    dynamodb_table: Table,
) -> None:
    """`record` produces an item with the documented partition / sort keys."""
    store = DynamoDBAuditStore(table_name="panakoes-audit-log", region_name="us-east-1")
    event = AuditEvent(
        timestamp=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
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
    await store.record(event)
    response = dynamodb_table.get_item(
        Key={
            "pk": "AUDIT#ingestion#user_abc",
            "sk": "2026-05-07T12:00:00+00:00#req_def",
        }
    )
    item = response["Item"]
    assert item["pk"] == "AUDIT#ingestion#user_abc"
    assert item["sk"] == "2026-05-07T12:00:00+00:00#req_def"
    assert item["actor_id"] == "user_abc"
    assert item["actor_type"] == "user"
    assert item["action"] == "transcript.created"
    assert item["resource_type"] == "transcript"
    assert item["resource_id"] == "trnscr_xyz"
    assert item["source_service"] == "ingestion"
    assert item["request_id"] == "req_def"
    assert item["source_ip"] == "203.0.113.42"
    assert item["details"] == {"audio_duration_seconds": 245}


@pytest.mark.integration
async def test_dynamodb_store_satisfies_audit_store_protocol(
    dynamodb_table: Table,
) -> None:
    """Structural typing: `DynamoDBAuditStore` is an `AuditStore`."""
    store = DynamoDBAuditStore(table_name="panakoes-audit-log", region_name="us-east-1")
    assert isinstance(store, AuditStore)


@pytest.mark.integration
async def test_dynamodb_store_records_multiple_events(
    dynamodb_table: Table,
) -> None:
    """Multiple events with distinct timestamps land as distinct items."""
    store = DynamoDBAuditStore(table_name="panakoes-audit-log", region_name="us-east-1")
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        await store.record(
            AuditEvent(
                timestamp=base.replace(second=i),
                actor_id="user_abc",
                actor_type="user",
                action="transcript.created",
                resource_type="transcript",
                resource_id=f"r{i}",
                source_service="ingestion",
                request_id=f"req_{i}",
            )
        )
    scan = dynamodb_table.scan()
    assert scan["Count"] == 3


@pytest.mark.integration
def test_dynamodb_store_exposes_constructor_arguments(
    dynamodb_table: Table,
) -> None:
    """Constructor args are reflected via the public properties."""
    assert dynamodb_table is not None  # consume fixture so moto is active
    store = DynamoDBAuditStore(table_name="custom", region_name="us-west-2")
    assert store.table_name == "custom"
    assert store.region_name == "us-west-2"
