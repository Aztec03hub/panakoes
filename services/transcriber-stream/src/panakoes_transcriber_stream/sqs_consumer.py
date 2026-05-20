"""Per-session SQS audio-frame consumer.

The streaming-router enqueues each ``audio-frame`` action as a UTF-8
JSON envelope on the session's claimed SQS queue. This module's job is
to long-poll that queue, decode the base64 PCM payload, drop frames
that pre-date the container's start, and yield raw PCM bytes to the
main loop.

Design v7, "Belt-and-suspenders: per-frame received_at filter at the
GPU consumer.": independent of the drain-then-claim guarantee the
gpu-spawner provides, this consumer checks each frame's ``received_at``
(set by ``streaming-router._route_audio_frame``) and silently drops any
frame older than ``container_started_at - 5.0s``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import boto3

logger = logging.getLogger(__name__)


class SQSConsumer:
    """Long-polls one SQS queue for ``audio-frame`` JSON messages.

    The ``frames()`` async iterator yields raw PCM bytes (typically
    6400 bytes per 200 ms frame at 16 kHz mono s16le). Each yielded
    chunk is the decoded payload from one SQS message; the message is
    deleted from the queue before the next iteration.
    """

    # Frames older than this many seconds before container start are
    # dropped at the consumer (defense in depth on top of the
    # drain-then-claim). Matches design v7 line referencing "5.0s".
    STALE_FRAME_CUTOFF_SECONDS = 5.0

    def __init__(
        self,
        queue_url: str,
        *,
        client: Any | None = None,
        wait_time_seconds: int = 20,
        max_messages: int = 10,
        started_at: float | None = None,
    ) -> None:
        self._queue_url = queue_url
        self._client = client
        self._wait_time_seconds = wait_time_seconds
        self._max_messages = max_messages
        self._started_at = started_at if started_at is not None else time.time()
        self._stop = asyncio.Event()
        self._stale_dropped = 0
        self._malformed_dropped = 0

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("sqs")
        return self._client

    def stop(self) -> None:
        """Signal the consumer to stop after the current receive batch."""

        self._stop.set()

    @property
    def stale_dropped(self) -> int:
        return self._stale_dropped

    @property
    def malformed_dropped(self) -> int:
        return self._malformed_dropped

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield PCM byte payloads as they arrive from the queue.

        Exits when ``stop()`` is called between receive batches.
        """

        loop = asyncio.get_running_loop()
        client = self._ensure_client()

        while not self._stop.is_set():
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda: client.receive_message(
                        QueueUrl=self._queue_url,
                        MaxNumberOfMessages=self._max_messages,
                        WaitTimeSeconds=self._wait_time_seconds,
                        AttributeNames=["All"],
                        MessageAttributeNames=["All"],
                    ),
                )
            except Exception:
                logger.exception("sqs_consumer_receive_failed")
                await asyncio.sleep(1.0)
                continue

            messages = resp.get("Messages") or []
            if not messages:
                continue

            to_delete: list[dict[str, str]] = []
            payloads: list[bytes] = []
            for msg in messages:
                msg_id = msg.get("MessageId", "?")
                receipt = msg.get("ReceiptHandle", "")
                body_raw = msg.get("Body", "")
                try:
                    envelope = json.loads(body_raw)
                except json.JSONDecodeError:
                    logger.info(
                        "sqs_consumer_drop_malformed_json",
                        extra={"message_id": msg_id},
                    )
                    self._malformed_dropped += 1
                    if receipt:
                        to_delete.append({"Id": msg_id, "ReceiptHandle": receipt})
                    continue

                # Stale-frame defense: drop frames captured before the
                # container started running.
                received_at = envelope.get("received_at")
                if isinstance(received_at, str):
                    try:
                        ts = datetime.fromisoformat(received_at.replace("Z", "+00:00")).timestamp()
                        if ts < self._started_at - self.STALE_FRAME_CUTOFF_SECONDS:
                            self._stale_dropped += 1
                            if receipt:
                                to_delete.append({"Id": msg_id, "ReceiptHandle": receipt})
                            continue
                    except ValueError:
                        # Malformed received_at: do not bounce on it.
                        pass

                pcm_b64 = envelope.get("pcm_b64", "")
                if not isinstance(pcm_b64, str) or not pcm_b64:
                    logger.info(
                        "sqs_consumer_drop_missing_pcm",
                        extra={"message_id": msg_id},
                    )
                    self._malformed_dropped += 1
                    if receipt:
                        to_delete.append({"Id": msg_id, "ReceiptHandle": receipt})
                    continue

                try:
                    pcm_bytes = base64.b64decode(pcm_b64, validate=True)
                except (ValueError, base64.binascii.Error):
                    logger.info(
                        "sqs_consumer_drop_bad_base64",
                        extra={"message_id": msg_id},
                    )
                    self._malformed_dropped += 1
                    if receipt:
                        to_delete.append({"Id": msg_id, "ReceiptHandle": receipt})
                    continue

                payloads.append(pcm_bytes)
                if receipt:
                    to_delete.append({"Id": msg_id, "ReceiptHandle": receipt})

            if to_delete:
                # Bind the current iteration's to_delete into a default
                # arg so the lambda does not capture by reference.
                try:
                    await loop.run_in_executor(
                        None,
                        lambda entries=to_delete: client.delete_message_batch(
                            QueueUrl=self._queue_url, Entries=entries
                        ),
                    )
                except Exception:
                    logger.exception("sqs_consumer_delete_batch_failed")

            for payload in payloads:
                yield payload
                if self._stop.is_set():
                    return
