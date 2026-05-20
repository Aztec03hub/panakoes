"""Unit tests for the SQS frame-queue pool claim protocol.

Uses moto-backed DynamoDB + SQS so the boto3 call paths exercise the
real client surfaces (conditional UpdateItem semantics, ReceiveMessage
batching). The 32-slot pool is provisioned per-test against a fresh
moto fixture; concurrent claimers exercise the conditional-check race
loop end-to-end.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import boto3
import pytest
from moto import mock_aws

from panakoes_gpu_spawner.pool_claim import (
    PoolClaim,
    PoolExhaustedError,
    _is_conditional_failure,
)

POOL_TABLE = "panakoes-test-stream-frame-pool"
POOL_SIZE = 32
REGION = "us-east-1"


def _provision_pool(*, size: int = POOL_SIZE) -> tuple[Any, Any, list[str]]:
    """Stand up the pool table + `size` SQS queues + populate the rows.

    Returns `(pool_table, sqs_client, queue_urls)`.
    """
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.create_table(
        TableName=POOL_TABLE,
        KeySchema=[{"AttributeName": "pool_queue_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pool_queue_id", "AttributeType": "N"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    sqs = boto3.client("sqs", region_name=REGION)
    urls: list[str] = []
    for i in range(size):
        url = sqs.create_queue(QueueName=f"panakoes-test-stream-frames-pool-{i}")["QueueUrl"]
        urls.append(url)
        table.put_item(Item={"pool_queue_id": i, "queue_url": url})
    return table, sqs, urls


@pytest.mark.unit
def test_claim_returns_a_queue_url_from_the_pool() -> None:
    """A fresh pool yields a queue URL to the first claimant."""
    with mock_aws():
        table, sqs, urls = _provision_pool()
        claim = PoolClaim(pool_table=table, sqs_client=sqs)

        result = claim.claim(session_id="sess-1")

        assert result in urls
        scan = table.scan()["Items"]
        claimed = [it for it in scan if "claimed_by" in it]
        assert len(claimed) == 1
        assert claimed[0]["claimed_by"] == "sess-1"
        assert claimed[0]["queue_url"] == result


@pytest.mark.unit
def test_claim_releases_pool_when_session_ends() -> None:
    """`release` clears `claimed_by` for the matching session."""
    with mock_aws():
        table, sqs, _urls = _provision_pool()
        claim = PoolClaim(pool_table=table, sqs_client=sqs)
        claim.claim(session_id="sess-1")
        # Find which row was claimed.
        scan = table.scan()["Items"]
        owned = next(it for it in scan if it.get("claimed_by") == "sess-1")
        pool_id = int(owned["pool_queue_id"])

        claim.release(pool_id, session_id="sess-1")

        after = table.get_item(Key={"pool_queue_id": pool_id})["Item"]
        assert "claimed_by" not in after
        assert "claimed_at" not in after


@pytest.mark.unit
def test_release_is_no_op_for_wrong_session() -> None:
    """A stale release from a non-owner is silently ignored."""
    with mock_aws():
        table, sqs, _urls = _provision_pool()
        claim = PoolClaim(pool_table=table, sqs_client=sqs)
        claim.claim(session_id="sess-owner")
        scan = table.scan()["Items"]
        owned = next(it for it in scan if it.get("claimed_by") == "sess-owner")
        pool_id = int(owned["pool_queue_id"])

        # A different session attempts release; the conditional update
        # fails silently (no exception) and the row stays owned.
        claim.release(pool_id, session_id="sess-imposter")

        after = table.get_item(Key={"pool_queue_id": pool_id})["Item"]
        assert after["claimed_by"] == "sess-owner"


@pytest.mark.unit
def test_claim_raises_when_pool_exhausted() -> None:
    """When every row is claimed, the next claim raises."""
    with mock_aws():
        table, sqs, _urls = _provision_pool(size=2)
        claim = PoolClaim(pool_table=table, sqs_client=sqs)
        claim.claim(session_id="sess-a")
        claim.claim(session_id="sess-b")

        with pytest.raises(PoolExhaustedError):
            claim.claim(session_id="sess-c")


@pytest.mark.unit
def test_claim_drains_residual_messages_before_returning() -> None:
    """A queue with stale messages is drained before the claimant uses it."""
    with mock_aws():
        table, sqs, urls = _provision_pool(size=1)
        # Seed the only queue with 3 stale frames.
        for i in range(3):
            sqs.send_message(QueueUrl=urls[0], MessageBody=f"stale-{i}")
        claim = PoolClaim(pool_table=table, sqs_client=sqs, drain_max_seconds=2.0)

        url = claim.claim(session_id="sess-1")

        # After drain the queue should be empty.
        resp = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=0, MaxNumberOfMessages=10)
        assert resp.get("Messages", []) == []


@pytest.mark.unit
def test_concurrent_claimers_get_distinct_queues_32_slot_pool() -> None:
    """5 concurrent claimers against the 32-slot pool each get distinct URLs."""
    with mock_aws():
        table, sqs, _urls = _provision_pool()
        claim = PoolClaim(pool_table=table, sqs_client=sqs, drain_max_seconds=0.1)

        # Use a barrier so the threads actually race against each other.
        barrier = threading.Barrier(5)
        results: list[str] = []
        lock = threading.Lock()

        def _claim_one(sid: str) -> None:
            barrier.wait()
            url = claim.claim(session_id=sid)
            with lock:
                results.append(url)

        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(_claim_one, [f"sess-{i}" for i in range(5)]))

        assert len(results) == 5
        # Five distinct queues; the conditional-update guarantees no
        # two claimants land on the same slot.
        assert len(set(results)) == 5
        scan = table.scan()["Items"]
        claimed_rows = [it for it in scan if "claimed_by" in it]
        assert len(claimed_rows) == 5


@pytest.mark.unit
def test_is_conditional_failure_matches_by_class_name() -> None:
    """`_is_conditional_failure` matches the dynamic boto3 exception class."""

    class ConditionalCheckFailedException(Exception):
        pass

    class SomethingElseError(Exception):
        pass

    assert _is_conditional_failure(ConditionalCheckFailedException("boom")) is True
    assert _is_conditional_failure(SomethingElseError("boom")) is False
    assert _is_conditional_failure(RuntimeError("boom")) is False
