"""Frame-queue pool claim + release for streaming sessions.

Per the design doc ("Frame-queue strategy") the streaming path uses a
pre-allocated pool of 32 standard SQS queues. Each session claims one
queue from the pool at spawn time via a DynamoDB conditional UpdateItem
on the `panakoes-dev-stream-frame-pool` table (one row per queue) and
releases it at lifecycle end. We never call `PurgeQueue`; instead the
claimant drains residual messages with a short bounded loop before the
GPU container starts consuming. See the design's "drain-then-claim"
section for the rationale.

This module is the pure-Python core of that protocol. AWS clients are
injected so tests can substitute moto-backed clients without monkey
patching boto3 at import time.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table
    from mypy_boto3_sqs.client import SQSClient


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for `claimed_at`."""
    return datetime.now(UTC).isoformat()


class PoolExhaustedError(RuntimeError):
    """Raised when every row in the pool table is already claimed."""


@dataclass(frozen=True)
class PoolClaimResult:
    """A successful claim, carrying both the queue URL and pool row id.

    The spawn callback needs the `queue_url` to wire into UserData + the
    streaming-sessions row, and the `pool_id` so it can release the slot
    if `RunInstances` fails after the claim succeeds.
    """

    queue_url: str
    pool_id: int


class PoolClaim:
    """Wrap the drain-then-claim protocol against DDB + SQS."""

    def __init__(
        self,
        *,
        pool_table: Table,
        sqs_client: SQSClient,
        drain_max_seconds: float = 3.0,
    ) -> None:
        """Bind the pool table + SQS client + drain budget.

        `drain_max_seconds` defaults to 3.0 per the adversarial round-3
        HIGH-03 fix; at the observed ~100 messages/sec drain throughput,
        3 seconds reliably clears a 250-message backlog (50 s of audio
        at 5 fps; realistic worst-case prior-session crash residue).
        """
        self._pool = pool_table
        self._sqs = sqs_client
        self._drain_max_seconds = drain_max_seconds

    def claim(self, session_id: str) -> PoolClaimResult:
        """Claim one pool queue for `session_id`; return the claim result.

        Returns a `PoolClaimResult` with both the SQS queue URL and the
        pool row id so the caller can `release(pool_id, session_id)` if
        a downstream step fails after the claim succeeds.

        The algorithm follows the design doc:

        1. Scan the pool table for rows without a `claimed_by` attr.
        2. Randomize the candidate list so concurrent claimers do not
           pile on the same slot.
        3. For each candidate, attempt a conditional UpdateItem. On
           success, fetch the queue URL, drain residual messages, and
           return the result. On `ConditionalCheckFailedException`,
           move to the next candidate.
        4. If every candidate loses its conditional race, the pool is
           exhausted; raise `PoolExhaustedError`.
        """
        candidates = self._scan_unclaimed()
        if not candidates:
            raise PoolExhaustedError("pool exhausted: no unclaimed rows")

        random.shuffle(candidates)
        for pool_id in candidates:
            try:
                self._pool.update_item(
                    Key={"pool_queue_id": pool_id},
                    UpdateExpression="SET claimed_by = :sid, claimed_at = :now",
                    ConditionExpression="attribute_not_exists(claimed_by)",
                    ExpressionAttributeValues={":sid": session_id, ":now": _now_iso()},
                )
            except Exception as exc:
                if _is_conditional_failure(exc):
                    continue
                raise

            row = self._pool.get_item(Key={"pool_queue_id": pool_id}).get("Item") or {}
            queue_url = row.get("queue_url")
            if not isinstance(queue_url, str) or not queue_url:
                # Mid-flight inconsistency: the row was claimed but has
                # no queue_url. Release and try the next candidate.
                self.release(pool_id, session_id)
                continue

            self._drain(queue_url)
            return PoolClaimResult(queue_url=queue_url, pool_id=pool_id)

        raise PoolExhaustedError("pool exhausted: every candidate lost the conditional race")

    def release(self, pool_id: int, session_id: str) -> None:
        """Release the claim on `pool_id` if it is owned by `session_id`.

        Conditional UpdateItem guarantees a stale release from a prior
        owner cannot accidentally release a queue claimed by a different
        session.
        """
        try:
            self._pool.update_item(
                Key={"pool_queue_id": pool_id},
                UpdateExpression="REMOVE claimed_by, claimed_at",
                ConditionExpression="claimed_by = :sid",
                ExpressionAttributeValues={":sid": session_id},
            )
        except Exception as exc:
            if _is_conditional_failure(exc):
                logger.info(
                    "pool release no-op: pool_id=%s not owned by %s",
                    pool_id,
                    session_id,
                )
                return
            raise

    def _scan_unclaimed(self) -> list[int]:
        """Return the list of pool ids with no `claimed_by` attribute.

        DDB Scan is eventually consistent; the conditional UpdateItem
        in `claim` is the authoritative ownership check. This pre-filter
        keeps the conditional-retry loop bounded by the count of
        actually-unclaimed slots instead of the full pool size.
        """
        resp = self._pool.scan(
            FilterExpression="attribute_not_exists(claimed_by)",
            ProjectionExpression="pool_queue_id",
        )
        candidates: list[int] = []
        for item in resp.get("Items", []):
            raw = item.get("pool_queue_id")
            try:
                candidates.append(int(raw))
            except (TypeError, ValueError):
                logger.warning("pool row has non-integer pool_queue_id: %r", raw)
        return candidates

    def _drain(self, queue_url: str) -> int:
        """Pull and discard residual messages up to `drain_max_seconds`.

        Returns the number of messages discarded. A return of 0 means
        the queue was empty within the deadline. The drain is bounded
        on wall-clock; if the queue keeps producing messages past the
        deadline (e.g. a stuck prior consumer is still publishing),
        the loop exits and the GPU container's per-frame `received_at`
        belt-and-suspenders filter handles any residue.
        """
        deadline = time.monotonic() + self._drain_max_seconds
        discarded = 0
        while time.monotonic() < deadline:
            resp = self._sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=0,
            )
            msgs = resp.get("Messages") or []
            if not msgs:
                return discarded
            entries = [
                {"Id": m["MessageId"], "ReceiptHandle": m["ReceiptHandle"]}
                for m in msgs
                if m.get("MessageId") and m.get("ReceiptHandle")
            ]
            if not entries:
                return discarded
            self._sqs.delete_message_batch(QueueUrl=queue_url, Entries=entries)
            discarded += len(entries)
        return discarded


def _is_conditional_failure(exc: Exception) -> bool:
    """Return True if `exc` is a DDB ConditionalCheckFailedException.

    boto3 raises the exception class dynamically off the client; the
    name is stable across botocore versions. We match on class name so
    this module works both against real boto3 clients and moto-backed
    fixtures without importing the botocore exception classes.
    """
    return type(exc).__name__ == "ConditionalCheckFailedException"
