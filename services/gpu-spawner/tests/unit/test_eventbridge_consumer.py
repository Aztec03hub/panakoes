"""Unit tests for the EventBridge ➜ SQS spawn-queue consumer.

Uses moto-backed SQS so the receive/delete loop exercises the boto3
call paths. The spawn callback is a test double that records intents
in a list; production wires this to the gpu-spawner spawn pipeline.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from panakoes_gpu_spawner.eventbridge_consumer import (
    EventBridgeConsumer,
    SpawnIntent,
)

REGION = "us-east-1"
QUEUE_NAME = "panakoes-test-spawn-queue"


def _provision_queue() -> tuple[Any, str]:
    """Stand up a moto-backed SQS queue and return `(client, url)`."""
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
    return sqs, url


def _eventbridge_envelope(*, session_id: str, user_id: str = "u1") -> str:
    """Build the JSON body EventBridge ➜ SQS produces."""
    return json.dumps(
        {
            "source": "panakoes.streaming-router",
            "detail-type": "streaming.session.connecting",
            "detail": {
                "session_id": session_id,
                "user_id": user_id,
                "tenant_id": "tenant-x",
            },
        }
    )


@pytest.mark.unit
def test_spawn_intent_from_event_detail_requires_session_id() -> None:
    """`from_event_detail` rejects a payload missing session_id."""
    with pytest.raises(ValueError, match="session_id"):
        SpawnIntent.from_event_detail({"user_id": "u1"})


@pytest.mark.unit
def test_spawn_intent_from_event_detail_succeeds_with_required_fields() -> None:
    """Optional fields collapse to empty strings; session_id is required."""
    intent = SpawnIntent.from_event_detail({"session_id": "sess-1"})
    assert intent.session_id == "sess-1"
    assert intent.user_id == ""
    assert intent.tenant_id == ""


@pytest.mark.unit
def test_poll_once_dispatches_intent_and_deletes_message() -> None:
    """A valid envelope drives the spawn callback then deletes the message."""
    with mock_aws():
        sqs, url = _provision_queue()
        sqs.send_message(QueueUrl=url, MessageBody=_eventbridge_envelope(session_id="sess-1"))

        received: list[SpawnIntent] = []

        consumer = EventBridgeConsumer(
            sqs_client=sqs,
            spawn_queue_url=url,
            spawn_callback=received.append,
            wait_time_seconds=0,
        )

        handled = consumer.poll_once()

        assert handled == 1
        assert len(received) == 1
        assert received[0].session_id == "sess-1"
        # Message should now be gone (delete_message succeeded).
        resp = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=0, MaxNumberOfMessages=1)
        assert resp.get("Messages", []) == []


@pytest.mark.unit
def test_poll_once_returns_zero_on_empty_queue() -> None:
    """An empty queue with a 0-second long-poll returns 0 immediately."""
    with mock_aws():
        sqs, url = _provision_queue()
        received: list[SpawnIntent] = []

        consumer = EventBridgeConsumer(
            sqs_client=sqs,
            spawn_queue_url=url,
            spawn_callback=received.append,
            wait_time_seconds=0,
        )

        handled = consumer.poll_once()

        assert handled == 0
        assert received == []


@pytest.mark.unit
def test_callback_exception_leaves_message_visible_for_redrive() -> None:
    """A spawn-callback raise leaves the message in-flight (no delete)."""
    with mock_aws():
        sqs, url = _provision_queue()
        sqs.send_message(QueueUrl=url, MessageBody=_eventbridge_envelope(session_id="sess-x"))

        def boom(_intent: SpawnIntent) -> None:
            raise RuntimeError("spawn explode")

        consumer = EventBridgeConsumer(
            sqs_client=sqs,
            spawn_queue_url=url,
            spawn_callback=boom,
            wait_time_seconds=0,
            # Short visibility so we can re-receive in the same test.
            visibility_timeout_seconds=1,
        )

        consumer.poll_once()

        # Wait until visibility expires; the message should reappear.
        import time

        time.sleep(1.2)
        # The load-bearing assertion is that delete_message was NOT called.
        # We assert that by checking the queue still reports at least one
        # message via attributes (either visible or in-flight).
        attrs = sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        total = int(attrs.get("ApproximateNumberOfMessages", "0")) + int(
            attrs.get("ApproximateNumberOfMessagesNotVisible", "0")
        )
        assert total >= 1


@pytest.mark.unit
def test_non_json_body_is_logged_and_dropped() -> None:
    """A garbage Body is logged + dropped without raising."""
    with mock_aws():
        sqs, url = _provision_queue()
        sqs.send_message(QueueUrl=url, MessageBody="not-json")
        received: list[SpawnIntent] = []
        consumer = EventBridgeConsumer(
            sqs_client=sqs,
            spawn_queue_url=url,
            spawn_callback=received.append,
            wait_time_seconds=0,
        )

        # Should not raise; the callback should not fire.
        consumer.poll_once()

        assert received == []


@pytest.mark.unit
def test_envelope_without_detail_is_dropped() -> None:
    """An EventBridge body without `detail` is logged + dropped."""
    with mock_aws():
        sqs, url = _provision_queue()
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"source": "x"}))
        received: list[SpawnIntent] = []
        consumer = EventBridgeConsumer(
            sqs_client=sqs,
            spawn_queue_url=url,
            spawn_callback=received.append,
            wait_time_seconds=0,
        )

        consumer.poll_once()

        assert received == []
