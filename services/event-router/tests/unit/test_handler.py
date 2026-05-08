"""Unit tests for `panakoes_event_router.handler`.

These are marked `unit` despite using moto because moto runs entirely
in-process: no containers, no network, fast enough for the unit tier.
"""

from __future__ import annotations

import json
from typing import Any, cast

import boto3
import pytest

from panakoes_event_router.config import load_settings
from panakoes_event_router.handler import EventRouter, lambda_handler
from tests.conftest import (
    TEST_BUS_NAME,
    TEST_REGION,
    EventBridgeSpy,
    make_eventbridge_s3_event,
    make_s3_event,
    seed_pending_record,
)


@pytest.mark.unit
def test_lambda_handler_happy_path(
    dynamodb_table: Any,
    eventbridge_bus: str,
) -> None:
    """End-to-end: pending record -> uploaded + EventBridge entry."""
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    event = make_s3_event(bucket="audio-uploads", key="audio/u1/i1/voice.wav")

    result = lambda_handler(event, context=object())

    assert result == {"statusCode": 200, "processed": 1}
    item = dynamodb_table.get_item(Key={"pk": "USER#u1", "sk": "INGESTION#i1"})["Item"]
    assert item["status"] == "uploaded"
    assert eventbridge_bus == TEST_BUS_NAME  # fixture consumed


@pytest.mark.unit
def test_publishes_event_with_expected_detail(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """The published EventBridge entry carries the documented payload."""
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    published = router.process(make_s3_event("audio-uploads", "audio/u1/i1/voice.wav"))

    assert published == 1
    assert len(event_spy.entries) == 1
    entry = event_spy.entries[0]
    assert entry["Source"] == "panakoes.ingest"
    assert entry["DetailType"] == "AudioUploaded"
    assert entry["EventBusName"] == eventbridge_bus
    detail = json.loads(entry["Detail"])
    assert detail == {"user_id": "u1", "ingestion_id": "i1", "filename": "voice.wav"}


@pytest.mark.unit
def test_idempotent_double_invocation(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """Re-running on the same S3 event produces exactly one EventBridge entry."""
    assert eventbridge_bus  # fixture consumed
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))
    event = make_s3_event("audio-uploads", "audio/u1/i1/voice.wav")

    first = router.process(event)
    second = router.process(event)

    assert first == 1
    assert second == 0  # second call no-ops via ConditionalCheckFailed
    assert len(event_spy.entries) == 1


@pytest.mark.unit
def test_unknown_record_emits_metric_and_skips(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """A key whose DDB row does not exist publishes nothing and emits the metric."""
    assert eventbridge_bus  # consume fixture
    # No seed: the row is intentionally absent.
    cw = boto3.client("cloudwatch", region_name=TEST_REGION)
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    published = router.process(make_s3_event("audio-uploads", "audio/u1/missing/voice.wav"))

    assert published == 0
    assert event_spy.entries == []
    # CloudWatch metric was published. moto records it; query via list_metrics.
    metrics = cw.list_metrics(Namespace="panakoes/dev/event-router")["Metrics"]
    metric_names = {m["MetricName"] for m in metrics}
    assert "unknown_record" in metric_names
    # Reason dimension is set so the operator can disambiguate.
    reasons = {
        d["Value"]
        for m in metrics
        if m["MetricName"] == "unknown_record"
        for d in m.get("Dimensions", [])
        if d["Name"] == "Reason"
    }
    assert "record_missing" in reasons


@pytest.mark.unit
def test_unparseable_key_emits_metric_and_skips(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """A key that does not match `audio/u/i/file` is reported and skipped."""
    assert dynamodb_table  # fixture consumed
    assert eventbridge_bus
    cw = boto3.client("cloudwatch", region_name=TEST_REGION)
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    published = router.process(make_s3_event("audio-uploads", "transcripts/u1/i1/x.txt"))

    assert published == 0
    assert event_spy.entries == []
    metrics = cw.list_metrics(Namespace="panakoes/dev/event-router")["Metrics"]
    reasons = {
        d["Value"]
        for m in metrics
        if m["MetricName"] == "unknown_record"
        for d in m.get("Dimensions", [])
        if d["Name"] == "Reason"
    }
    assert "unparseable_key" in reasons


@pytest.mark.unit
def test_skips_record_when_status_already_uploaded(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """A row already in `uploaded` is not re-published (idempotent re-delivery)."""
    assert eventbridge_bus
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    # Pre-flip the status as if a prior invocation succeeded.
    dynamodb_table.update_item(
        Key={"pk": "USER#u1", "sk": "INGESTION#i1"},
        UpdateExpression="SET #s = :uploaded",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":uploaded": "uploaded"},
    )
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    published = router.process(make_s3_event("audio-uploads", "audio/u1/i1/voice.wav"))

    assert published == 0
    assert event_spy.entries == []


@pytest.mark.unit
def test_processes_eventbridge_wrapped_event(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """When S3 sends through EventBridge, the wrapper shape is decoded correctly."""
    assert eventbridge_bus
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    published = router.process(
        make_eventbridge_s3_event("audio-uploads", "audio/u1/i1/voice.wav")
    )

    assert published == 1
    assert len(event_spy.entries) == 1


@pytest.mark.unit
def test_processes_multiple_records_in_one_event(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """A batch of S3 records yields one EventBridge entry each."""
    assert eventbridge_bus
    seed_pending_record(dynamodb_table, user_id="u1", ingestion_id="i1")
    seed_pending_record(dynamodb_table, user_id="u2", ingestion_id="i2")
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))
    event: dict[str, Any] = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "audio-uploads"},
                    "object": {"key": "audio/u1/i1/voice.wav"},
                },
            },
            {
                "s3": {
                    "bucket": {"name": "audio-uploads"},
                    "object": {"key": "audio/u2/i2/note.m4a"},
                },
            },
        ]
    }

    published = router.process(event)

    assert published == 2
    assert {json.loads(e["Detail"])["user_id"] for e in event_spy.entries} == {"u1", "u2"}


@pytest.mark.unit
def test_returns_zero_for_empty_event(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """Events without any parseable S3 records do nothing, still return statusCode 200."""
    assert dynamodb_table
    assert eventbridge_bus
    settings = load_settings()
    router = EventRouter(settings, events_client=cast(Any, event_spy))

    assert router.process({}) == 0
    assert router.process({"Records": "not-a-list"}) == 0
    assert router.process({"Records": [{}]}) == 0  # missing s3 key
    assert router.process({"Records": [{"s3": "wrong-shape"}]}) == 0
    assert router.process({"source": "aws.s3", "detail": "wrong-shape"}) == 0
    assert router.process({"source": "aws.s3", "detail": {"bucket": {}, "object": {}}}) == 0
    assert event_spy.entries == []


@pytest.mark.unit
def test_metric_publication_failure_does_not_break_handler(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """If CloudWatch put_metric_data raises, we swallow it and keep going."""
    assert dynamodb_table
    assert eventbridge_bus
    settings = load_settings()

    class ExplodingCloudWatch:
        def put_metric_data(self, **_: Any) -> None:
            from botocore.exceptions import ClientError

            raise ClientError(
                error_response={"Error": {"Code": "Throttling", "Message": "x"}},
                operation_name="PutMetricData",
            )

    router = EventRouter(
        settings,
        events_client=cast(Any, event_spy),
        cloudwatch_client=cast(Any, ExplodingCloudWatch()),
    )

    # Use an unparseable key so the unknown-record path is exercised.
    published = router.process(make_s3_event("audio-uploads", "transcripts/wrong.wav"))
    assert published == 0
    assert event_spy.entries == []


@pytest.mark.unit
def test_unhandled_dynamodb_error_propagates(
    dynamodb_table: Any,
    eventbridge_bus: str,
    event_spy: EventBridgeSpy,
) -> None:
    """A non-conditional DynamoDB ClientError surfaces (let Lambda retry)."""
    assert dynamodb_table
    assert eventbridge_bus
    from botocore.exceptions import ClientError

    settings = load_settings()

    class ExplodingTable:
        def update_item(self, **_: Any) -> None:
            raise ClientError(
                error_response={"Error": {"Code": "ProvisionedThroughputExceeded", "Message": "x"}},
                operation_name="UpdateItem",
            )

    class ExplodingResource:
        def Table(self, _name: str) -> Any:
            return ExplodingTable()

    router = EventRouter(
        settings,
        dynamodb_resource=cast(Any, ExplodingResource()),
        events_client=cast(Any, event_spy),
    )

    with pytest.raises(ClientError):
        router.process(make_s3_event("audio-uploads", "audio/u1/i1/voice.wav"))
