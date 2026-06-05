"""Tests for the SQSConsumer (moto-backed)."""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import time
from typing import Any

import pytest

from panakoes_transcriber_stream.sqs_consumer import SQSConsumer

_SEQ = itertools.count(1)


def _send_frame(
    sqs_client: Any,
    queue_url: str,
    *,
    pcm: bytes,
    received_at: str | None = None,
    seq: int | None = None,
) -> None:
    envelope: dict[str, Any] = {
        "action": "audio-frame",
        "v": 1,
        "seq": seq if seq is not None else next(_SEQ),
        "ts_ms_delta": 0,
        "pcm_b64": base64.b64encode(pcm).decode("ascii"),
    }
    if received_at is not None:
        envelope["received_at"] = received_at
    sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(envelope))


@pytest.mark.asyncio
async def test_frames_yields_decoded_payloads(sqs_client: Any, sqs_queue: str) -> None:
    _send_frame(sqs_client, sqs_queue, pcm=b"abc")
    _send_frame(sqs_client, sqs_queue, pcm=b"defghi")

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            if len(collected) >= 2:
                consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert b"abc" in collected
    assert b"defghi" in collected


@pytest.mark.asyncio
async def test_frames_drops_stale_received_at(sqs_client: Any, sqs_queue: str) -> None:
    stale_iso = "1970-01-01T00:00:00Z"
    _send_frame(sqs_client, sqs_queue, pcm=b"stale", received_at=stale_iso)
    _send_frame(sqs_client, sqs_queue, pcm=b"fresh")

    consumer = SQSConsumer(
        sqs_queue, client=sqs_client, wait_time_seconds=0, started_at=time.time()
    )
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert b"fresh" in collected
    assert b"stale" not in collected
    assert consumer.stale_dropped == 1


@pytest.mark.asyncio
async def test_frames_drops_malformed_json(sqs_client: Any, sqs_queue: str) -> None:
    sqs_client.send_message(QueueUrl=sqs_queue, MessageBody="this is not json")
    _send_frame(sqs_client, sqs_queue, pcm=b"good")

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"good"]
    assert consumer.malformed_dropped == 1


@pytest.mark.asyncio
async def test_frames_drops_missing_pcm_b64(sqs_client: Any, sqs_queue: str) -> None:
    sqs_client.send_message(
        QueueUrl=sqs_queue,
        MessageBody=json.dumps({"action": "audio-frame", "v": 1}),
    )
    _send_frame(sqs_client, sqs_queue, pcm=b"good")

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"good"]
    assert consumer.malformed_dropped == 1


@pytest.mark.asyncio
async def test_frames_drops_bad_base64(sqs_client: Any, sqs_queue: str) -> None:
    sqs_client.send_message(
        QueueUrl=sqs_queue,
        MessageBody=json.dumps({"action": "audio-frame", "v": 1, "pcm_b64": "not!!base64$$"}),
    )
    _send_frame(sqs_client, sqs_queue, pcm=b"good")

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"good"]
    assert consumer.malformed_dropped == 1


@pytest.mark.asyncio
async def test_stop_short_circuits_loop(sqs_client: Any, sqs_queue: str) -> None:
    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    consumer.stop()
    collected: list[bytes] = []
    async for payload in consumer.frames():
        collected.append(payload)
    assert collected == []


@pytest.mark.asyncio
async def test_malformed_received_at_does_not_drop(sqs_client: Any, sqs_queue: str) -> None:
    _send_frame(sqs_client, sqs_queue, pcm=b"fresh", received_at="not-a-timestamp")
    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    # Malformed received_at falls through to the normal path; the payload
    # is yielded and stale_dropped stays at 0.
    assert collected == [b"fresh"]
    assert consumer.stale_dropped == 0


@pytest.mark.asyncio
async def test_frames_reorders_by_seq(sqs_client: Any, sqs_queue: str) -> None:
    """Out-of-order arrivals yield in seq order (standard SQS reorders)."""
    _send_frame(sqs_client, sqs_queue, pcm=b"third", seq=12)
    _send_frame(sqs_client, sqs_queue, pcm=b"first", seq=10)
    _send_frame(sqs_client, sqs_queue, pcm=b"second", seq=11)

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            if len(collected) >= 3:
                consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"first", b"second", b"third"]


@pytest.mark.asyncio
async def test_frames_drops_duplicate_seq(sqs_client: Any, sqs_queue: str) -> None:
    """At-least-once redelivery of an already-yielded seq is dropped."""
    _send_frame(sqs_client, sqs_queue, pcm=b"one", seq=20)
    _send_frame(sqs_client, sqs_queue, pcm=b"one-dup", seq=20)
    _send_frame(sqs_client, sqs_queue, pcm=b"two", seq=21)

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            if len(collected) >= 2:
                consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"one", b"two"]


@pytest.mark.asyncio
async def test_frames_skips_persistent_gap(sqs_client: Any, sqs_queue: str) -> None:
    """A seq gap older than MAX_GAP_WAIT_SECONDS is skipped, not stalled on."""
    _send_frame(sqs_client, sqs_queue, pcm=b"a", seq=30)
    # seq 31 never arrives.
    _send_frame(sqs_client, sqs_queue, pcm=b"c", seq=32)

    consumer = SQSConsumer(sqs_queue, client=sqs_client, wait_time_seconds=0)
    consumer.MAX_GAP_WAIT_SECONDS = 0.0  # immediate skip for the test
    collected: list[bytes] = []

    async def collect() -> None:
        async for payload in consumer.frames():
            collected.append(payload)
            if len(collected) >= 2:
                consumer.stop()

    await asyncio.wait_for(collect(), timeout=5.0)
    assert collected == [b"a", b"c"]
