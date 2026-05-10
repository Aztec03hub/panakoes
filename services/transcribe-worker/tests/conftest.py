"""Shared pytest fixtures for the Transcribe Worker Lambda.

Per ADR-018 we exercise the real boto3 surface against `moto` instead
of stubbing client methods. moto stands up DynamoDB and S3 in-memory.
For the transcriber we inject a fake `Transcriber` (the real backend
makes outbound HTTPS to Groq, which is out of scope for unit tests).
"""

from __future__ import annotations

import json
import os

# Pin `OTEL_SDK_DISABLED=true` BEFORE any test module imports the handler.
# The handler runs `panakoes_otel.configure()` at module-import time as
# its Lambda cold-start hook; install NoOp providers during tests so no
# exporter sockets open.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws
from panakoes_transcriber import (
    TranscriberAuthError,
    TranscriberRateLimitError,
    TranscriptionResult,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

TEST_TABLE_NAME = "panakoes-ingestion-test"
TEST_BUCKET = "panakoes-dev-audio-uploads-test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "DDB_INGESTION_TABLE",
        "AUDIO_UPLOADS_BUCKET",
        "TRANSCRIBER_BACKEND",
        "GROQ_API_KEY",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DDB_INGESTION_TABLE", TEST_TABLE_NAME)
    monkeypatch.setenv("AUDIO_UPLOADS_BUCKET", TEST_BUCKET)
    monkeypatch.setenv("TRANSCRIBER_BACKEND", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("AWS_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    yield


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto for every AWS service this Lambda touches."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mocks: None) -> Table:
    """Provision the ingestion DynamoDB table inside the moto mock."""
    assert aws_mocks is None
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
def s3_bucket(aws_mocks: None) -> str:
    """Provision the audio-uploads bucket and return its name."""
    assert aws_mocks is None
    client = boto3.client("s3", region_name=TEST_REGION)
    client.create_bucket(Bucket=TEST_BUCKET)
    return TEST_BUCKET


def seed_pending_record(
    table: Any,
    *,
    user_id: str,
    ingestion_id: str,
    filename: str = "voice.wav",
    transcript_status: str | None = None,
) -> None:
    """Seed an ingestion record so the worker has something to update.

    `transcript_status` may be:
      - None: no transcript yet (fresh upload)
      - "pending" / "succeeded" / "failed"
    """
    item: dict[str, Any] = {
        "pk": f"USER#{user_id}",
        "sk": f"INGESTION#{ingestion_id}",
        "ingestion_id": ingestion_id,
        "user_id": user_id,
        "filename": filename,
        "content_type": "audio/wav",
        "size_bytes": 1024,
        "s3_key": f"audio/{user_id}/{ingestion_id}/{filename}",
        "status": "uploaded",
        "created_at": "2026-05-09T20:00:00+00:00",
        "updated_at": "2026-05-09T20:00:00+00:00",
    }
    if transcript_status is not None:
        item["transcript_status"] = transcript_status
    table.put_item(Item=item)


def seed_audio_object(
    *,
    bucket: str,
    user_id: str,
    ingestion_id: str,
    filename: str = "voice.wav",
    body: bytes = b"\x00\x01\x02\x03",
) -> None:
    """Put a tiny audio object at the expected key."""
    client = boto3.client("s3", region_name=TEST_REGION)
    client.put_object(
        Bucket=bucket,
        Key=f"audio/{user_id}/{ingestion_id}/{filename}",
        Body=body,
    )


def make_sqs_event_for(
    *,
    bucket: str,
    user_id: str,
    ingestion_id: str,
    filename: str = "voice.wav",
    message_id: str = "sqs-msg-1",
) -> dict[str, Any]:
    """Build a minimal SQS event whose body is an EventBridge S3 envelope."""
    eventbridge_envelope = {
        "version": "0",
        "id": "abc",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "111111111111",
        "time": "2026-05-09T20:00:00Z",
        "region": TEST_REGION,
        "resources": [f"arn:aws:s3:::{bucket}"],
        "detail": {
            "version": "0",
            "bucket": {"name": bucket},
            "object": {
                "key": f"audio/{user_id}/{ingestion_id}/{filename}",
                "size": 1024,
            },
        },
    }
    return {
        "Records": [
            {
                "messageId": message_id,
                "receiptHandle": "rh",
                "body": json.dumps(eventbridge_envelope),
                "attributes": {},
                "messageAttributes": {},
                "md5OfBody": "x",
                "eventSource": "aws:sqs",
                "eventSourceARN": f"arn:aws:sqs:{TEST_REGION}:111111111111:q",
                "awsRegion": TEST_REGION,
            }
        ]
    }


class FakeTranscriber:
    """Async-callable stand-in for `Transcriber` used by tests."""

    def __init__(
        self,
        *,
        result: TranscriptionResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.result = result or TranscriptionResult(
            text="hello world",
            segments=(),
            language="en",
            duration_seconds=1.5,
        )
        self.raises = raises
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, *, audio_bytes: bytes, filename: str) -> TranscriptionResult:
        self.calls.append((audio_bytes, filename))
        if self.raises is not None:
            raise self.raises
        return self.result


def patch_get_transcriber(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeTranscriber,
) -> None:
    """Replace the env-var dispatch with a fixed fake.

    Patches BOTH the source location (panakoes_ingestion_api.transcription.get_transcriber)
    AND the worker's re-import (panakoes_transcribe_worker.handler.get_transcriber)
    because Python caches the latter at import time.
    """
    monkeypatch.setattr(
        "panakoes_transcribe_worker.handler.get_transcriber",
        lambda: fake,
    )


# Re-export error classes so tests need only one import.
__all__ = [
    "TEST_BUCKET",
    "TEST_REGION",
    "TEST_TABLE_NAME",
    "FakeTranscriber",
    "TranscriberAuthError",
    "TranscriberRateLimitError",
    "make_sqs_event_for",
    "patch_get_transcriber",
    "seed_audio_object",
    "seed_pending_record",
]
