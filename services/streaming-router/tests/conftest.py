"""Shared pytest fixtures for the streaming-router Lambda.

Uses moto-backed DynamoDB + SQS + EventBridge to exercise the real
AWS SDK call paths without hitting real AWS. The streaming-sessions
table schema mirrors `infra/dev/data/main.tf` (hash key `session_id`).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

TEST_TABLE = "panakoes-test-streaming-sessions"
TEST_QUEUE = "panakoes-test-streaming-frames"
TEST_REGION = "us-east-1"
TEST_EVENT_BUS = "default"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STREAMING_SESSIONS_TABLE", TEST_TABLE)
    monkeypatch.setenv("STREAMING_EVENT_BUS", TEST_EVENT_BUS)
    monkeypatch.setenv("AWS_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    yield


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    with mock_aws():
        yield


@pytest.fixture
def sessions_table(aws_mocks: None) -> Any:
    """A moto-backed streaming-sessions DynamoDB table."""
    assert aws_mocks is None
    ddb = boto3.resource("dynamodb", region_name=TEST_REGION)
    table = ddb.create_table(
        TableName=TEST_TABLE,
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def frame_queue(aws_mocks: None, monkeypatch: pytest.MonkeyPatch) -> str:
    """A moto-backed SQS queue + its URL pinned into the env var."""
    assert aws_mocks is None
    sqs = boto3.client("sqs", region_name=TEST_REGION)
    queue_url = sqs.create_queue(QueueName=TEST_QUEUE)["QueueUrl"]
    monkeypatch.setenv("AUDIO_FRAME_QUEUE_URL", queue_url)
    return queue_url


@pytest.fixture
def sqs_client(aws_mocks: None) -> Any:
    assert aws_mocks is None
    return boto3.client("sqs", region_name=TEST_REGION)


@pytest.fixture
def events_client(aws_mocks: None) -> Any:
    assert aws_mocks is None
    return boto3.client("events", region_name=TEST_REGION)


def make_event(
    *,
    route_key: str,
    connection_id: str = "conn-1",
    body: str | None = None,
    user_id: str = "user_abc",
    tenant_id: str = "tenant_xyz",
    role: str = "user",
    event_type: str | None = None,
) -> dict[str, Any]:
    """Build an API Gateway v2 WebSocket event."""
    if event_type is None:
        event_type = {
            "$connect": "CONNECT",
            "$disconnect": "DISCONNECT",
        }.get(route_key, "MESSAGE")
    request_ctx: dict[str, Any] = {
        "routeKey": route_key,
        "connectionId": connection_id,
        "eventType": event_type,
        "domainName": "abc.execute-api.us-east-1.amazonaws.com",
        "stage": "dev",
        "authorizer": {
            "lambda": {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
            }
        },
    }
    event: dict[str, Any] = {"requestContext": request_ctx}
    if body is not None:
        event["body"] = body
    return event
