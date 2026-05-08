"""Shared pytest fixtures for the Event Router Lambda.

Per ADR-018 we exercise the real boto3 surface against `moto` instead
of stubbing client methods. moto stands up DynamoDB, EventBridge, and
CloudWatch in-memory; tests inject a small `EventBridgeSpy` to inspect
the exact wire entries published, since moto does not expose a public
read-side for `put_events` calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

TEST_TABLE_NAME = "panakoes-ingestion-test"
TEST_BUS_NAME = "panakoes-ingest-test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "DDB_INGESTION_TABLE",
        "EVENTBRIDGE_BUS_NAME",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DDB_INGESTION_TABLE", TEST_TABLE_NAME)
    monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", TEST_BUS_NAME)
    monkeypatch.setenv("AWS_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    # moto wants *something* in the AWS creds slots so boto3 init is happy.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    yield


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto for every AWS service this Lambda touches."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mocks: None) -> Table:
    """Provision the ingestion DynamoDB table inside the moto mock."""
    assert aws_mocks is None  # consume the fixture so moto is active
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
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_TABLE_NAME)


@pytest.fixture
def eventbridge_bus(aws_mocks: None) -> str:
    """Provision the custom EventBridge bus inside the moto mock."""
    assert aws_mocks is None
    client = boto3.client("events", region_name=TEST_REGION)
    client.create_event_bus(Name=TEST_BUS_NAME)
    return TEST_BUS_NAME


class EventBridgeSpy:
    """boto3-shaped EventBridge client that records every `put_events` call.

    moto correctly accepts `put_events` for buses we created, but it
    does not expose a read-side API for the events themselves. Tests
    that need to assert on the exact wire entries inject this spy via
    `EventRouter(events_client=...)`.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.entries.extend(Entries)
        return {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": f"evt-{i}"} for i in range(len(Entries))],
        }


@pytest.fixture
def event_spy() -> EventBridgeSpy:
    """Fresh spy per test; tests pass it as `events_client` to the router."""
    return EventBridgeSpy()


def make_s3_event(bucket: str, key: str) -> dict[str, Any]:
    """Build a minimal S3 ObjectCreated event with one record."""
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "size": 1024},
                },
            }
        ]
    }


def make_eventbridge_s3_event(bucket: str, key: str) -> dict[str, Any]:
    """Build a minimal EventBridge-wrapped S3 ObjectCreated event."""
    return {
        "version": "0",
        "id": "abc",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "111111111111",
        "time": "2026-05-07T20:00:00Z",
        "region": TEST_REGION,
        "resources": [f"arn:aws:s3:::{bucket}"],
        "detail": {
            "version": "0",
            "bucket": {"name": bucket},
            "object": {"key": key, "size": 1024},
        },
    }


def seed_pending_record(
    table: Any,
    *,
    user_id: str,
    ingestion_id: str,
    filename: str = "voice.wav",
) -> None:
    """Seed a `pending` ingestion record so the handler has something to flip."""
    table.put_item(
        Item={
            "pk": f"USER#{user_id}",
            "sk": f"INGESTION#{ingestion_id}",
            "ingestion_id": ingestion_id,
            "user_id": user_id,
            "filename": filename,
            "content_type": "audio/wav",
            "size_bytes": 1024,
            "s3_key": f"audio/{user_id}/{ingestion_id}/{filename}",
            "status": "pending",
            "created_at": "2026-05-07T20:00:00+00:00",
            "updated_at": "2026-05-07T20:00:00+00:00",
        }
    )
