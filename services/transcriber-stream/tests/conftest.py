"""Shared test fixtures for the transcriber-stream service.

moto fixtures are scoped to ``function`` so each test starts with a
clean AWS-mock world. The ``valid_env`` fixture pre-populates the
required env vars for ``load_config_from_env``; tests that want to
override a single value do so via ``monkeypatch.setenv``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_credentials_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force boto3 to use canned dummy creds so it never reaches real AWS."""

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Populate the required env vars for ``load_config_from_env``."""

    env: dict[str, str] = {
        "PANAKOES_SESSION_ID": "sess_test1234567890ab",
        "PANAKOES_CONNECTION_ID": "conn_test1234567890ab",
        "FRAME_QUEUE_URL": (
            "https://sqs.us-east-1.amazonaws.com/123456789012/panakoes-dev-stream-frames-pool-0"
        ),
        "WS_ENDPOINT": "https://aaaa1111.execute-api.us-east-1.amazonaws.com/dev",
        "STREAMING_SESSIONS_TABLE": "panakoes-dev-streaming-sessions",
        "STREAMING_FRAME_POOL_TABLE": "panakoes-dev-stream-frame-pool",
        "TRANSCRIPTS_BUCKET": "panakoes-dev-transcripts",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture
def aws(valid_env: dict[str, str]) -> Iterator[None]:
    """Activate moto for the AWS services this service touches."""

    with mock_aws():
        yield


@pytest.fixture
def s3_client(aws: None) -> Any:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="panakoes-dev-transcripts")
    return client


@pytest.fixture
def sqs_client(aws: None) -> Any:
    client = boto3.client("sqs", region_name="us-east-1")
    return client


@pytest.fixture
def sqs_queue(sqs_client: Any) -> str:
    resp = sqs_client.create_queue(QueueName="panakoes-dev-stream-frames-pool-0")
    return resp["QueueUrl"]


@pytest.fixture
def ddb_resource(aws: None) -> Any:
    return boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture
def sessions_table(ddb_resource: Any) -> Any:
    table = ddb_resource.create_table(
        TableName="panakoes-dev-streaming-sessions",
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    table.put_item(
        Item={
            "session_id": "sess_test1234567890ab",
            "connection_id": "conn_test1234567890ab",
            "status": "connecting",
            "created_at": "2026-05-20T00:00:00Z",
            "updated_at": "2026-05-20T00:00:00Z",
        }
    )
    return table


@pytest.fixture
def repo_root() -> str:
    """Absolute path to the service root (where pyproject.toml lives)."""

    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
