"""Unit tests for the DynamoDB-backed `LifecycleStateStore`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from panakoes_admin_api.lifecycle_state import (
    DEFAULT_TTL_SECONDS,
    LifecycleStateStore,
)
from panakoes_admin_api.models import LifecycleStatus


@pytest.fixture
def store() -> Iterator[LifecycleStateStore]:
    """Moto-backed lifecycle-state table."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-lifecycle-state",
            KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "idempotency_key", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield LifecycleStateStore(table=table)


@pytest.mark.unit
def test_get_returns_none_on_miss(store: LifecycleStateStore) -> None:
    """A missing idempotency_key yields None cleanly."""
    assert store.get("does-not-exist") is None


@pytest.mark.unit
def test_put_pending_then_get_roundtrip(store: LifecycleStateStore) -> None:
    """Put a pending row, get returns the same envelope."""
    seed = store.put_pending(
        idempotency_key="op-1",
        audit_request_id="req-1",
    )
    fetched = store.get("op-1")
    assert fetched is not None
    assert fetched.idempotency_key == "op-1"
    assert fetched.status == LifecycleStatus.PENDING
    assert fetched.audit_request_id == "req-1"
    assert fetched.started_at == seed.started_at


@pytest.mark.unit
def test_put_pending_writes_expires_at_with_default_ttl(
    store: LifecycleStateStore,
) -> None:
    """Default put writes `expires_at = now + 24h`."""
    store.put_pending(idempotency_key="op-2", audit_request_id="req-2")
    raw = store._table.get_item(Key={"idempotency_key": "op-2"})
    item = raw.get("Item")
    assert item is not None
    expires_at = int(item["expires_at"])
    now_seconds = int(datetime.now(UTC).timestamp())
    assert (
        DEFAULT_TTL_SECONDS - 60
        <= expires_at - now_seconds
        <= DEFAULT_TTL_SECONDS + 60
    )


@pytest.mark.unit
def test_update_terminal_promotes_to_succeeded(store: LifecycleStateStore) -> None:
    """A pending row updates to SUCCEEDED with a result envelope."""
    seed = store.put_pending(idempotency_key="op-3", audit_request_id="req-3")
    finalized = store.update_terminal(
        idempotency_key="op-3",
        status=LifecycleStatus.SUCCEEDED,
        result={"affected": 1},
        audit_request_id="req-3",
        started_at=seed.started_at,
    )
    assert finalized.status == LifecycleStatus.SUCCEEDED
    assert finalized.result == {"affected": 1}
    assert finalized.error_message is None
    assert finalized.finished_at is not None
    # Round-trip via get to confirm persistence.
    fetched = store.get("op-3")
    assert fetched is not None
    assert fetched.status == LifecycleStatus.SUCCEEDED


@pytest.mark.unit
def test_update_terminal_records_failure_with_message(
    store: LifecycleStateStore,
) -> None:
    """A failed operation persists the error message."""
    seed = store.put_pending(idempotency_key="op-4", audit_request_id="req-4")
    finalized = store.update_terminal(
        idempotency_key="op-4",
        status=LifecycleStatus.FAILED,
        error_message="downstream timed out",
        audit_request_id="req-4",
        started_at=seed.started_at,
    )
    assert finalized.status == LifecycleStatus.FAILED
    assert finalized.error_message == "downstream timed out"
    assert finalized.result is None


@pytest.mark.unit
def test_update_terminal_rejects_non_terminal_status(
    store: LifecycleStateStore,
) -> None:
    """Calling update_terminal with PENDING or RUNNING raises ValueError."""
    seed = store.put_pending(idempotency_key="op-5", audit_request_id="req-5")
    with pytest.raises(ValueError, match="terminal status"):
        store.update_terminal(
            idempotency_key="op-5",
            status=LifecycleStatus.PENDING,
            audit_request_id="req-5",
            started_at=seed.started_at,
        )
