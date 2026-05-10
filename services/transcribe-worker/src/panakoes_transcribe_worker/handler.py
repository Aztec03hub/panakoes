"""Lambda entrypoint for the Panakoes Transcribe Worker.

Triggered by an SQS event-source mapping. The SQS queue is fed by an
EventBridge rule on the default bus that matches S3 ObjectCreated
events on the audio-uploads bucket (per `infra/dev/transcribe-worker/`).
That means each SQS record's body is the JSON-stringified EventBridge
envelope around the S3 event, NOT a raw S3 record. We parse it
accordingly.

Flow per record:

  1. Decode `record["body"]` as the EventBridge wrapper.
  2. Extract the S3 bucket + object key.
  3. Parse the key (`audio/{user_id}/{ingestion_id}/{filename}`).
  4. Mark the ingestion record `transcript_status=pending` (idempotent
     short-circuit on `succeeded`; matches the on-demand route's
     pre-schedule semantics).
  5. Call `transcribe_ingestion()` to do the actual work. The same
     function backs the on-demand `POST /api/v1/transcribe/{id}` route
     so behavior stays single-sourced (ADR-009).

Failure handling matches AWS's `ReportBatchItemFailures` SQS contract:

  - Transient errors (rate limit) re-raise so the message stays in
    flight and SQS retries after the visibility timeout.
  - Terminal errors (auth, upstream, malformed key, missing record)
    are logged + `transcript_status` is set to `failed` by
    `transcribe_ingestion()`, then we return success so SQS deletes
    the message. The DLQ is reserved for surprise crashes
    (after 3 receive attempts per the queue's redrive policy).

This split reflects what's actionable from a re-delivery standpoint:
re-running a Groq auth failure produces the same auth failure; the
operator sees the failure surfaced through the DynamoDB row and the
front-end, and DLQ noise stays signal-rich.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.transcription import (
    get_transcriber,
    transcribe_ingestion,
)
from panakoes_otel import configure as otel_configure
from panakoes_otel import instrument_boto3
from panakoes_transcriber import TranscriberRateLimitError

from panakoes_transcribe_worker.config import Settings, load_settings
from panakoes_transcribe_worker.key_parser import ParsedKey, parse_object_key

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# OpenTelemetry cold-start hook. Mirrors the event-router pattern: configure
# once per warm container, never call shutdown (Lambda tears the container
# down on its own schedule and a manual flush would block invocations
# behind exporter I/O). instrument_boto3 covers DynamoDB + SQS + S3 because
# they all sit on top of botocore.
# ---------------------------------------------------------------------------
_OTEL_CONFIGURED: bool = False


def _ensure_otel_configured() -> None:
    """Configure OTel once per container; subsequent calls are no-ops."""
    global _OTEL_CONFIGURED
    if _OTEL_CONFIGURED:
        return
    otel_configure(
        service_name="transcribe-worker",
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_boto3()
    _OTEL_CONFIGURED = True


try:
    _ensure_otel_configured()
except Exception:  # observability must never fail the handler
    logger.exception("panakoes-otel cold-start configure failed; continuing")


def _extract_s3_event_from_sqs_body(body: str) -> dict[str, Any] | None:
    """Parse an SQS record body into the inner S3 bucket + key dict.

    Two shapes are accepted, in this order:

      1. EventBridge wrapper: the standard shape when an EventBridge rule
         targets an SQS queue. `body` is the EB envelope JSON; the S3
         bucket + key live under `detail.bucket.name` + `detail.object.key`.
      2. Raw S3 notification: when (rarely) the queue is wired as a direct
         S3 notification target instead of through EventBridge. `body`
         carries an `{"Records": [...]}` document.

    Returns `None` if the body does not match either shape; the caller
    treats that as a routing miss (logged and dropped, not retried).
    """
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    # EventBridge envelope shape
    if payload.get("source") == "aws.s3" and isinstance(payload.get("detail"), dict):
        detail = payload["detail"]
        bucket = (detail.get("bucket") or {}).get("name")
        obj = detail.get("object") or {}
        key = obj.get("key")
        if isinstance(bucket, str) and isinstance(key, str):
            return {"bucket": bucket, "key": key}
        return None

    # Raw S3 notification shape (defensive fallback)
    records = payload.get("Records")
    if isinstance(records, list) and records:
        first = records[0]
        if isinstance(first, dict):
            s3 = first.get("s3")
            if isinstance(s3, dict):
                bucket = (s3.get("bucket") or {}).get("name")
                key = (s3.get("object") or {}).get("key")
                if isinstance(bucket, str) and isinstance(key, str):
                    return {"bucket": bucket, "key": key}

    return None


class TranscribeWorker:
    """Stateful worker; constructed once per Lambda warm container.

    Holding boto3 / store / transcriber instances on the worker keeps
    connection pools warm across invocations on the same container,
    while still letting tests inject fakes for everything.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store: IngestionStore | None = None,
    ) -> None:
        self._settings = settings
        if store is None:
            store = IngestionStore(
                table_name=settings.ddb_ingestion_table,
                region_name=settings.aws_region,
            )
        self._store = store

    def process_record(self, record: dict[str, Any]) -> str | None:
        """Process one SQS record. Return the messageId iff it must be retried.

        Returning `None` signals success (SQS deletes the message).
        Returning the messageId signals "transient failure, leave it in
        flight"; the caller adds it to the `batchItemFailures` response so
        SQS retries that one record without disturbing the rest of the batch.
        """
        message_id = record.get("messageId", "")
        body = record.get("body", "")
        if not isinstance(body, str) or not body:
            logger.warning("transcribe-worker: empty SQS body", extra={"messageId": message_id})
            return None

        s3_info = _extract_s3_event_from_sqs_body(body)
        if s3_info is None:
            logger.warning(
                "transcribe-worker: unparseable SQS body",
                extra={"messageId": message_id},
            )
            return None

        bucket = s3_info["bucket"]
        if bucket != self._settings.audio_uploads_bucket:
            # Defensive: the EventBridge rule already filters by bucket, but
            # mis-routed messages (e.g. someone hand-poked a test message)
            # should drop cleanly rather than blow up downstream.
            logger.warning(
                "transcribe-worker: bucket mismatch; dropping",
                extra={
                    "messageId": message_id,
                    "expected_bucket": self._settings.audio_uploads_bucket,
                    "got_bucket": bucket,
                },
            )
            return None

        parsed = parse_object_key(s3_info["key"])
        if parsed is None:
            logger.warning(
                "transcribe-worker: key did not match audio/ layout",
                extra={"messageId": message_id, "raw_key": s3_info["key"]},
            )
            return None

        return self._dispatch(parsed, message_id=message_id)

    def _dispatch(self, parsed: ParsedKey, *, message_id: str) -> str | None:
        """Run `transcribe_ingestion` against the parsed upload.

        Idempotency: if the record is already `succeeded` we short-circuit
        (re-delivery, double-fire, retry of an upstream success). If it
        is currently `pending` another invocation is in flight; re-running
        is a no-op for correctness but wastes Groq cost, so we skip too.
        """
        record = self._store.get(parsed.user_id, parsed.ingestion_id)
        if record is None:
            logger.warning(
                "transcribe-worker: ingestion record missing",
                extra={
                    "messageId": message_id,
                    "user_id": parsed.user_id,
                    "ingestion_id": parsed.ingestion_id,
                },
            )
            return None

        existing_status = record.transcript_status
        if existing_status == "succeeded":
            logger.info(
                "transcribe-worker: skipping; transcript already succeeded",
                extra={"ingestion_id": parsed.ingestion_id},
            )
            return None
        if existing_status == "pending":
            logger.info(
                "transcribe-worker: skipping; transcript already in-flight",
                extra={"ingestion_id": parsed.ingestion_id},
            )
            return None

        # Mark pending so a concurrent re-delivery short-circuits above.
        self._store.set_transcript_pending(parsed.user_id, parsed.ingestion_id)

        try:
            transcriber = get_transcriber()
            asyncio.run(
                transcribe_ingestion(
                    ingestion_id=parsed.ingestion_id,
                    user_id=parsed.user_id,
                    transcriber=transcriber,
                    store=self._store,
                    bucket=self._settings.audio_uploads_bucket,
                    region_name=self._settings.aws_region,
                )
            )
        except TranscriberRateLimitError:
            # Transient. Re-raise as a soft signal via batchItemFailures so
            # SQS retries this record after the visibility timeout. The
            # `transcribe_ingestion` call itself converts most errors into
            # `transcript_status=failed`; rate limits surface earlier
            # because they can be raised from `get_transcriber()` adjacent
            # constructs or by the backend before the orchestration's own
            # try/except sees them.
            logger.warning(
                "transcribe-worker: rate-limited; will retry via SQS",
                extra={"ingestion_id": parsed.ingestion_id, "messageId": message_id},
            )
            return message_id
        except Exception:
            # Anything else is logged but not re-raised: `transcribe_ingestion`
            # already persists `transcript_status=failed` for known backend
            # errors. Surprise exceptions from import/init paths flow here;
            # we swallow to avoid hammering Lambda retries against the same
            # broken state, and the SQS message is deleted so it does not
            # poison the queue.
            logger.exception(
                "transcribe-worker: unexpected error dispatching transcription",
                extra={"ingestion_id": parsed.ingestion_id, "messageId": message_id},
            )
            return None

        return None

    def process(self, event: dict[str, Any]) -> dict[str, Any]:
        """Process every SQS record in `event`.

        Returns the AWS-required `batchItemFailures` response shape so SQS
        knows which messages (if any) to retain for retry. An empty list
        means "everything succeeded; delete the whole batch".
        """
        records = event.get("Records") or []
        if not isinstance(records, list):
            return {"batchItemFailures": []}

        retry_ids: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            failed_id = self.process_record(record)
            if failed_id:
                retry_ids.append({"itemIdentifier": failed_id})
        return {"batchItemFailures": retry_ids}


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint.

    `context` is the standard `LambdaContext` object; unused today but
    kept on the signature so static analyzers and future enrichment
    work. The return value MUST match the SQS `ReportBatchItemFailures`
    shape (`{"batchItemFailures": [{"itemIdentifier": "..."}, ...]}`)
    because the event-source mapping is configured with that response
    type for selective re-queue.
    """
    del context
    settings = load_settings()
    worker = TranscribeWorker(settings)
    return worker.process(event)


__all__ = ["TranscribeWorker", "lambda_handler"]
