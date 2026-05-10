"""Unit tests for `panakoes_transcribe_worker.handler`.

Marked `unit` despite using moto because moto runs entirely in-process:
no containers, no network, fast enough for the unit tier.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from panakoes_transcribe_worker.handler import (
    TranscribeWorker,
    _extract_s3_event_from_sqs_body,
    lambda_handler,
)
from tests.conftest import (
    FakeTranscriber,
    TranscriberAuthError,
    TranscriberRateLimitError,
    make_sqs_event_for,
    patch_get_transcriber,
    seed_audio_object,
    seed_pending_record,
)

# ---------------------------------------------------------------------------
# _extract_s3_event_from_sqs_body
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_eventbridge_envelope() -> None:
    body = json.dumps(
        {
            "source": "aws.s3",
            "detail-type": "Object Created",
            "detail": {
                "bucket": {"name": "b"},
                "object": {"key": "audio/u/i/v.wav"},
            },
        }
    )
    assert _extract_s3_event_from_sqs_body(body) == {
        "bucket": "b",
        "key": "audio/u/i/v.wav",
    }


@pytest.mark.unit
def test_extract_raw_s3_records() -> None:
    body = json.dumps(
        {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "b"},
                        "object": {"key": "audio/u/i/v.wav"},
                    }
                }
            ]
        }
    )
    assert _extract_s3_event_from_sqs_body(body) == {
        "bucket": "b",
        "key": "audio/u/i/v.wav",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        "",
        "not-json",
        "[]",
        "{}",
        json.dumps({"source": "aws.s3", "detail": {}}),
        json.dumps({"Records": []}),
        json.dumps({"Records": [{"no-s3": True}]}),
    ],
)
def test_extract_returns_none_on_unparseable(body: str) -> None:
    assert _extract_s3_event_from_sqs_body(body) is None


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lambda_handler_happy_path(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end with moto: SQS event -> transcript persisted, no retries."""
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    seed_audio_object(bucket=s3_bucket, user_id="u1", ingestion_id="i1")

    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(bucket=s3_bucket, user_id="u1", ingestion_id="i1")
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": []}
    item = dynamodb_table.get_item(Key={"pk": "USER#u1", "sk": "INGESTION#i1"})["Item"]
    assert item["transcript_status"] == "succeeded"
    assert item["transcript"]["text"] == "hello world"
    assert len(fake.calls) == 1


@pytest.mark.unit
def test_idempotent_against_already_succeeded(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-delivery on a finished record short-circuits without re-running."""
    seed_pending_record(
        dynamodb_table,
        user_id="u1",
        ingestion_id="i1",
        transcript_status="succeeded",
    )
    seed_audio_object(bucket=s3_bucket, user_id="u1", ingestion_id="i1")

    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(bucket=s3_bucket, user_id="u1", ingestion_id="i1")
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": []}
    assert fake.calls == []  # transcriber not invoked


@pytest.mark.unit
def test_idempotent_against_in_flight_pending(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending row means another invocation is in-flight; do not re-run."""
    seed_pending_record(
        dynamodb_table,
        user_id="u1",
        ingestion_id="i1",
        transcript_status="pending",
    )
    seed_audio_object(bucket=s3_bucket, user_id="u1", ingestion_id="i1")

    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(bucket=s3_bucket, user_id="u1", ingestion_id="i1")
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": []}
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rate_limit_surfaces_via_batch_item_failures(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limits keep the message in flight for SQS to retry."""
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    seed_audio_object(bucket=s3_bucket, user_id="u1", ingestion_id="i1")

    # Wrap get_transcriber to raise rate-limit at construction time so the
    # error escapes the inner transcribe_ingestion try/except.
    def _raise_rate_limit() -> Any:
        raise TranscriberRateLimitError("slow down", retry_after_seconds=1.0)

    monkeypatch.setattr(
        "panakoes_transcribe_worker.handler.get_transcriber",
        _raise_rate_limit,
    )

    event = make_sqs_event_for(
        bucket=s3_bucket,
        user_id="u1",
        ingestion_id="i1",
        message_id="msg-rl",
    )
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-rl"}]}


@pytest.mark.unit
def test_auth_error_does_not_retry(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth failures are terminal: persisted as `failed`, no retry."""
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    seed_audio_object(bucket=s3_bucket, user_id="u1", ingestion_id="i1")

    fake = FakeTranscriber(raises=TranscriberAuthError("bad key"))
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(bucket=s3_bucket, user_id="u1", ingestion_id="i1")
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": []}
    item = dynamodb_table.get_item(Key={"pk": "USER#u1", "sk": "INGESTION#i1"})["Item"]
    assert item["transcript_status"] == "failed"
    assert "TranscriberAuthError" in item["transcript_error_message"]


@pytest.mark.unit
def test_malformed_key_logged_and_dropped(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keys not matching `audio/...` drop without retry or DDB write."""
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = {
        "Records": [
            {
                "messageId": "m1",
                "body": json.dumps(
                    {
                        "source": "aws.s3",
                        "detail-type": "Object Created",
                        "detail": {
                            "bucket": {"name": s3_bucket},
                            "object": {"key": "thumbnails/cover.jpg"},
                        },
                    }
                ),
            }
        ]
    }
    result = lambda_handler(event, context=object())
    assert result == {"batchItemFailures": []}
    assert fake.calls == []


@pytest.mark.unit
def test_unknown_record_logged_and_dropped(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key whose ingestion id is not in DDB drops without retry."""
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(bucket=s3_bucket, user_id="ghost", ingestion_id="ghost-i")
    result = lambda_handler(event, context=object())

    assert result == {"batchItemFailures": []}
    assert fake.calls == []


@pytest.mark.unit
def test_bucket_mismatch_dropped(
    dynamodb_table: Any,
    s3_bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message claiming a different bucket name is dropped defensively."""
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)

    event = make_sqs_event_for(
        bucket="some-other-bucket",
        user_id="u1",
        ingestion_id="i1",
    )
    result = lambda_handler(event, context=object())
    assert result == {"batchItemFailures": []}
    assert fake.calls == []


@pytest.mark.unit
def test_empty_body_dropped(
    monkeypatch: pytest.MonkeyPatch,
    aws_mocks: None,
) -> None:
    """An SQS record with an empty body is logged + dropped."""
    assert aws_mocks is None
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)
    event = {"Records": [{"messageId": "m1", "body": ""}]}
    # We need a worker; lambda_handler would fail because moto's tables
    # are not set up, but we never reach the store. Still, lambda_handler
    # constructs a worker (which constructs a Store with moto/no table).
    # Construction is fine; only get/update calls would fail.
    result = lambda_handler(event, context=object())
    assert result == {"batchItemFailures": []}


@pytest.mark.unit
def test_unparseable_body_dropped(
    monkeypatch: pytest.MonkeyPatch,
    aws_mocks: None,
) -> None:
    """An SQS record whose body is not valid JSON is logged + dropped."""
    assert aws_mocks is None
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)
    event = {"Records": [{"messageId": "m1", "body": "not-json-at-all"}]}
    result = lambda_handler(event, context=object())
    assert result == {"batchItemFailures": []}


@pytest.mark.unit
def test_handler_skips_non_dict_records(
    monkeypatch: pytest.MonkeyPatch,
    aws_mocks: None,
) -> None:
    """A malformed Records array that contains non-dict entries is skipped."""
    assert aws_mocks is None
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)
    event = {"Records": ["nope", 42, None]}
    result = lambda_handler(event, context=object())
    assert result == {"batchItemFailures": []}


@pytest.mark.unit
def test_handler_handles_missing_records_key(
    monkeypatch: pytest.MonkeyPatch,
    aws_mocks: None,
) -> None:
    """An event without Records (e.g. test ping) returns an empty failure list."""
    assert aws_mocks is None
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)
    result = lambda_handler({}, context=object())
    assert result == {"batchItemFailures": []}


@pytest.mark.unit
def test_handler_handles_records_not_a_list(
    monkeypatch: pytest.MonkeyPatch,
    aws_mocks: None,
) -> None:
    """A weirdly-shaped event whose Records key is not a list returns empty."""
    assert aws_mocks is None
    fake = FakeTranscriber()
    patch_get_transcriber(monkeypatch, fake)
    result = lambda_handler({"Records": "not-a-list"}, context=object())
    assert result == {"batchItemFailures": []}


# ---------------------------------------------------------------------------
# Constructor surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_worker_constructible_without_explicit_store(
    dynamodb_table: Any,
) -> None:
    """The default constructor wires its own IngestionStore against env settings."""
    from panakoes_transcribe_worker.config import load_settings

    worker = TranscribeWorker(load_settings())
    # No assertion on private state; the existence + lack of exception is the contract.
    assert worker is not None
    assert dynamodb_table is not None  # fixture consumed
