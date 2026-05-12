"""Shared test fixtures for the transcriber-batch service.

The moto fixtures here are deliberately scoped to ``function`` so each
test starts with a clean S3 + DynamoDB world. The required env vars
for the Settings constructor are pre-populated by the
``valid_settings_env`` fixture; tests that need to override a single
value do so via ``monkeypatch.setenv``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

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
def valid_settings_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Populate the required env vars for :func:`load_settings`."""
    env = {
        "S3_INPUT_URI": "s3://panakoes-dev-audio-uploads/audio/user_abc/ing_xyz/file.wav",
        "S3_OUTPUT_PREFIX": "s3://panakoes-dev-transcripts/sess_test1234567890ab",
        "JOB_ID": "batch-job-0001",
        "SESSION_ID": "sess_test1234567890ab",
        "SESSIONS_TABLE": "panakoes-dev-streaming-sessions",
        "MODEL_PATH": "/opt/whisper/models/large-v3.pt",
        "DEVICE": "cpu",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture
def aws(valid_settings_env: dict[str, str]) -> Iterator[None]:
    """Activate moto for all AWS services this service touches."""
    with mock_aws():
        yield


@pytest.fixture
def s3_client(aws: None) -> object:
    """Return an S3 client wired to the moto mock and pre-create buckets."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="panakoes-dev-audio-uploads")
    client.create_bucket(Bucket="panakoes-dev-transcripts")
    return client


@pytest.fixture
def sessions_table(aws: None) -> object:
    """Create the streaming-sessions DDB table on moto and return the Table resource."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.create_table(
        TableName="panakoes-dev-streaming-sessions",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    table.put_item(
        Item={
            "id": "sess_test1234567890ab",
            "user_id": "usr_test1234567890ab",
            "status": "starting",
            "created_at": "2026-05-11T00:00:00Z",
            "updated_at": "2026-05-11T00:00:00Z",
        }
    )
    return table


@pytest.fixture
def audio_object(s3_client: object) -> str:
    """Put a placeholder audio payload in the input bucket and return its URI."""
    bucket = "panakoes-dev-audio-uploads"
    key = "audio/user_abc/ing_xyz/file.wav"
    s3_client.put_object(Bucket=bucket, Key=key, Body=b"RIFF....fake-wav-bytes")  # type: ignore[attr-defined]
    return f"s3://{bucket}/{key}"


@pytest.fixture
def fake_whisper_result() -> dict[str, object]:
    """Return a representative whisper.transcribe output for mocking."""
    return {
        "text": " hello world this is a test ",
        "language": "en",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": " hello world",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.5},
                ],
            },
            {
                "id": 1,
                "start": 1.5,
                "end": 3.2,
                "text": " this is a test",
                "words": [
                    {"word": "this", "start": 1.5, "end": 1.8},
                    {"word": "is", "start": 1.9, "end": 2.1},
                    {"word": "a", "start": 2.2, "end": 2.4},
                    {"word": "test", "start": 2.5, "end": 3.2},
                ],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _suppress_otel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable real OTel exporter sockets during tests."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    # Guard against environment leakage from the host shell.
    for var in ("OTEL_EXPORTER_OTLP_ENDPOINT",):
        if var in os.environ:
            monkeypatch.delenv(var, raising=False)
