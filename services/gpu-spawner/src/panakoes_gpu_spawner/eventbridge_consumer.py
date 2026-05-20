"""SQS consumer driving auto-spawn off the streaming.session.connecting event.

EventBridge fans `streaming.session.connecting` (emitted by the
streaming-router Lambda on `$connect`) into a dedicated SQS queue
(`panakoes-dev-spawn-queue`). This module owns the consumer loop the
gpu-spawner ECS task runs alongside its FastAPI HTTP surface: every
inbound SQS message corresponds to one session-spawn intent, identified
by `detail.session_id`.

The consumer is intentionally synchronous in shape (one message ->
one spawn) so that:

- failures isolate per-session (a bad spawn does not poison-pill the
  whole loop, the message goes back to visibility-timeout for retry
  and ultimately the DLQ);
- the side-effects (DDB row update, pool claim, EC2 RunInstances) are
  testable against moto without orchestrating an asyncio runtime.

The orchestrator that wires this up registers the consumer as a
background task via `consume_forever()`; tests drive `process_message()`
in isolation.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpawnIntent:
    """Decoded event payload describing one session-spawn intent."""

    session_id: str
    user_id: str
    tenant_id: str

    @classmethod
    def from_event_detail(cls, detail: dict[str, Any]) -> SpawnIntent:
        """Parse the EventBridge `detail` envelope into a SpawnIntent.

        EventBridge wraps the publisher's `Detail` payload as a nested
        JSON object under `detail`. We require `session_id` (the only
        non-optional field for spawn dispatch); `user_id` + `tenant_id`
        are surfaced for observability and downstream tagging.
        """
        session_id = detail.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("event detail missing session_id")
        return cls(
            session_id=session_id,
            user_id=str(detail.get("user_id") or ""),
            tenant_id=str(detail.get("tenant_id") or ""),
        )


SpawnCallback = Callable[[SpawnIntent], None]


class EventBridgeConsumer:
    """Pulls EventBridge ➜ SQS messages and invokes the spawn callback."""

    def __init__(
        self,
        *,
        sqs_client: SQSClient,
        spawn_queue_url: str,
        spawn_callback: SpawnCallback,
        wait_time_seconds: int = 20,
        max_messages_per_poll: int = 1,
        visibility_timeout_seconds: int | None = None,
    ) -> None:
        """Bind the SQS client + downstream callback.

        `wait_time_seconds` enables long-polling so the loop costs
        nothing when the queue is empty. `max_messages_per_poll=1`
        keeps the spawn batch sequential which matches the per-session
        EC2 RunInstances ceiling we want to honour in v1.
        """
        self._sqs = sqs_client
        self._queue_url = spawn_queue_url
        self._spawn = spawn_callback
        self._wait_time_seconds = wait_time_seconds
        self._max_messages = max_messages_per_poll
        self._visibility_timeout = visibility_timeout_seconds

    def poll_once(self) -> int:
        """Pull at most `max_messages_per_poll` messages and dispatch.

        Returns the number of messages handled. A return of 0 indicates
        the long-poll timed out with no messages, which is the steady-
        state idle path.
        """
        kwargs: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MaxNumberOfMessages": self._max_messages,
            "WaitTimeSeconds": self._wait_time_seconds,
        }
        if self._visibility_timeout is not None:
            kwargs["VisibilityTimeout"] = self._visibility_timeout
        resp = self._sqs.receive_message(**kwargs)
        messages = resp.get("Messages") or []
        for message in messages:
            self.process_message(message)
        return len(messages)

    def process_message(self, message: dict[str, Any]) -> None:
        """Decode + dispatch one SQS message.

        On callback success the message is deleted. On any callback
        failure we log + leave the message visible so SQS redrives it
        per the queue's retry policy; the consumer does not delete on
        failure (the redrive policy + DLQ are the safety net).
        """
        body_raw = message.get("Body")
        receipt = message.get("ReceiptHandle")
        if not isinstance(body_raw, str) or not isinstance(receipt, str):
            logger.warning(
                "spawn-queue message missing Body or ReceiptHandle: %s",
                message.get("MessageId"),
            )
            return
        try:
            envelope = json.loads(body_raw)
        except json.JSONDecodeError:
            logger.exception(
                "spawn-queue message Body is not JSON: %s",
                message.get("MessageId"),
            )
            return

        detail = envelope.get("detail")
        if not isinstance(detail, dict):
            logger.warning(
                "spawn-queue message has no detail object: %s",
                message.get("MessageId"),
            )
            return

        try:
            intent = SpawnIntent.from_event_detail(detail)
        except ValueError as exc:
            logger.warning(
                "spawn-queue message rejected (%s): %s",
                exc,
                message.get("MessageId"),
            )
            return

        try:
            self._spawn(intent)
        except Exception:
            logger.exception(
                "spawn callback failed for session_id=%s; leaving message visible for SQS redrive",
                intent.session_id,
            )
            return

        self._sqs.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt)

    def consume_forever(self, *, sleep_between_polls: float = 0.0) -> None:
        """Run `poll_once` in a loop until interrupted.

        The orchestrator runs this in a dedicated thread alongside the
        FastAPI uvicorn loop. `sleep_between_polls` is 0 by default
        because SQS long-polling already gates the iteration cadence;
        a non-zero value is occasionally useful in tests.
        """
        while True:  # pragma: no cover - exercised via process_message
            try:
                self.poll_once()
            except Exception:
                logger.exception("eventbridge-consumer poll failed")
            if sleep_between_polls:
                time.sleep(sleep_between_polls)
