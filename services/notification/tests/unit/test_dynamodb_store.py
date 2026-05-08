"""Unit tests for `panakoes_notification.storage.dynamodb.NotificationStore`.

These tests run under `mock_aws` so they technically integrate with a
fake DynamoDB. We classify them as unit because they do not exercise
any HTTP surface or other services.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws
from ulid import ULID

from panakoes_notification.models import NotificationRecord
from panakoes_notification.storage.dynamodb import NotificationStore
from tests.conftest import TEST_REGION, TEST_TABLE_NAME


@pytest.fixture
def store() -> Any:
    """Return a fresh `NotificationStore` with a moto-backed table."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name=TEST_REGION)
        client.create_table(
            TableName=TEST_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield NotificationStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)


@pytest.mark.unit
def test_store_table_name_property(store: NotificationStore) -> None:
    """`table_name` exposes the configured value."""
    assert store.table_name == TEST_TABLE_NAME


@pytest.mark.unit
def test_store_put_then_get_roundtrips(store: NotificationStore) -> None:
    """A put followed by a get returns an equivalent record."""
    record = NotificationRecord(
        notification_id=str(ULID()),
        user_id="user_a",
        channel="email",
        status="sent",
        target="alice@example.com",
        subject="Welcome",
        template="welcome",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    store.put(record)
    fetched = store.get(record.user_id, record.notification_id)
    assert fetched is not None
    assert fetched.notification_id == record.notification_id
    assert fetched.target == record.target
    assert fetched.subject == record.subject


@pytest.mark.unit
def test_store_get_returns_none_for_missing_record(store: NotificationStore) -> None:
    """An unknown id returns `None`."""
    assert store.get("user_a", str(ULID())) is None


@pytest.mark.unit
def test_store_list_returns_only_callers_records(store: NotificationStore) -> None:
    """Listing for user A does not include user B's records."""
    record_a = NotificationRecord(
        notification_id=str(ULID()),
        user_id="user_a",
        channel="email",
        status="sent",
        target="alice@example.com",
        subject="x",
        template="welcome",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    record_b = NotificationRecord(
        notification_id=str(ULID()),
        user_id="user_b",
        channel="email",
        status="sent",
        target="bob@example.com",
        subject="x",
        template="welcome",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    store.put(record_a)
    store.put(record_b)
    items_a, _ = store.list_for_user("user_a", limit=10)
    assert len(items_a) == 1
    assert items_a[0].user_id == "user_a"


@pytest.mark.unit
def test_store_list_paginates_with_cursor(store: NotificationStore) -> None:
    """A small limit returns a cursor that fetches the next page."""
    for _ in range(5):
        store.put(
            NotificationRecord(
                notification_id=str(ULID()),
                user_id="user_a",
                channel="webhook",
                status="sent",
                target="https://example.com/hook",
                attempts=1,
                created_at=datetime.now(UTC),
            )
        )
        time.sleep(0.001)  # ensure ULIDs differ in time component
    first_page, cursor = store.list_for_user("user_a", limit=2)
    assert len(first_page) == 2
    assert cursor is not None
    second_page, _ = store.list_for_user("user_a", limit=2, cursor=cursor)
    assert len(second_page) == 2
    first_ids = {r.notification_id for r in first_page}
    second_ids = {r.notification_id for r in second_page}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.unit
def test_store_list_returns_newest_first(store: NotificationStore) -> None:
    """ULID sort + `ScanIndexForward=False` => newest record at index 0."""
    older_id = str(ULID())
    time.sleep(0.005)
    newer_id = str(ULID())
    store.put(
        NotificationRecord(
            notification_id=older_id,
            user_id="user_a",
            channel="email",
            status="sent",
            target="alice@example.com",
            attempts=1,
            created_at=datetime.now(UTC),
        )
    )
    store.put(
        NotificationRecord(
            notification_id=newer_id,
            user_id="user_a",
            channel="email",
            status="sent",
            target="alice@example.com",
            attempts=1,
            created_at=datetime.now(UTC),
        )
    )
    items, _ = store.list_for_user("user_a", limit=10)
    assert items[0].notification_id == newer_id
    assert items[1].notification_id == older_id
