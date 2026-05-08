"""Lambda entrypoint for the Panakoes Event Router.

Triggered by S3 `ObjectCreated:*` notifications on the audio-uploads
bucket. Two trigger shapes are supported because Terraform may wire
S3 -> Lambda directly or S3 -> EventBridge -> Lambda:

  - Direct: `event["Records"][i]` carries the raw S3 record.
  - EventBridge wrapper: `event["source"] == "aws.s3"` and the bucket /
    object live under `event["detail"]`.

For each S3 record:

  1. Parse the object key to recover `user_id` + `ingestion_id`.
  2. Conditionally update the matching DynamoDB record from
     `status=pending` to `status=uploaded`. The condition makes the
     handler idempotent: re-running on the same event is a no-op.
  3. Publish a `panakoes.ingest / AudioUploaded` EventBridge event so
     the transcription pipeline can pick the upload up.

Records that do not parse, or whose DDB row is missing, are logged and
counted via a CloudWatch metric (`unknown_record`) but do not fail the
batch. Records whose row exists but is not in `pending` are skipped
silently (idempotent re-delivery).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError
from panakoes_otel import configure as otel_configure
from panakoes_otel import instrument_boto3

from panakoes_event_router.config import Settings, load_settings
from panakoes_event_router.key_parser import ParsedKey, parse_object_key

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch.client import CloudWatchClient
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table
    from mypy_boto3_events.client import EventBridgeClient

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)


# Lambda-specific OpenTelemetry wiring.
#
# `configure()` runs once per cold-started container and is guarded so a
# warm-container re-import never reconfigures providers. We do NOT call
# `shutdown()`; the Lambda runtime tears the container down on its own
# schedule, and a manual flush would block invocations behind exporter
# I/O. `instrument_boto3()` covers DynamoDB, EventBridge, and CloudWatch
# automatically because they all sit on top of botocore.
_OTEL_CONFIGURED: bool = False


def _ensure_otel_configured() -> None:
    """Configure OTel once per container; subsequent calls are no-ops.

    The library's own `is_configured()` guard would suffice, but we
    keep an explicit module-level Once-guard so the intent is local
    to the handler and unit-testable without touching library state.
    """
    global _OTEL_CONFIGURED
    if _OTEL_CONFIGURED:
        return
    otel_configure(
        service_name="event-router",
        environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
    )
    instrument_boto3()
    _OTEL_CONFIGURED = True


# Cold-start hook: fire the wiring on the first import of this module.
# Wrapped in `try` so a transient instrumentation failure never breaks
# the function; observability is best-effort, the routing path is not.
try:
    _ensure_otel_configured()
except Exception:  # observability must never fail the handler
    logger.exception("panakoes-otel cold-start configure failed; continuing")


# ---------------------------------------------------------------------------
# DynamoDB key construction. Mirrors services/ingestion-api/.../dynamodb.py
# so a single change to the schema is a search-replace away. Duplicating a
# four-line helper is cheaper than coupling two services through a shared
# `panakoes-storage` library at this stage.
# ---------------------------------------------------------------------------
def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _ingestion_sk(ingestion_id: str) -> str:
    return f"INGESTION#{ingestion_id}"


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the input event to a list of `{bucket, key}` dicts.

    Accepts the two shapes Terraform may use to invoke us. Returns an
    empty list for any other shape (e.g. test harness ping) so the
    handler stays robust.
    """
    if event.get("source") == "aws.s3" and isinstance(event.get("detail"), dict):
        detail = event["detail"]
        bucket = (detail.get("bucket") or {}).get("name")
        obj = detail.get("object") or {}
        key = obj.get("key")
        if isinstance(bucket, str) and isinstance(key, str):
            return [{"bucket": bucket, "key": key}]
        return []

    records = event.get("Records")
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        s3 = rec.get("s3")
        if not isinstance(s3, dict):
            continue
        bucket = (s3.get("bucket") or {}).get("name")
        key = (s3.get("object") or {}).get("key")
        if isinstance(bucket, str) and isinstance(key, str):
            out.append({"bucket": bucket, "key": key})
    return out


class EventRouter:
    """Stateful router; constructed once per Lambda warm container.

    Holding boto3 clients on an instance keeps connection pools warm
    across invocations on the same container, while still letting tests
    inject moto-backed clients.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        dynamodb_resource: DynamoDBServiceResource | None = None,
        events_client: EventBridgeClient | None = None,
        cloudwatch_client: CloudWatchClient | None = None,
    ) -> None:
        self._settings = settings
        if dynamodb_resource is None:
            dynamodb_resource = boto3.resource("dynamodb", region_name=settings.aws_region)
        self._table: Table = dynamodb_resource.Table(settings.ddb_ingestion_table)
        self._events: EventBridgeClient = events_client or boto3.client(
            "events", region_name=settings.aws_region
        )
        self._cloudwatch: CloudWatchClient = cloudwatch_client or boto3.client(
            "cloudwatch", region_name=settings.aws_region
        )

    def process(self, event: dict[str, Any]) -> int:
        """Process every S3 record in `event`; return the count published."""
        records = _extract_s3_records(event)
        published = 0
        for rec in records:
            if self._handle_one(rec["key"]):
                published += 1
        return published

    # ---- private helpers -------------------------------------------------
    def _handle_one(self, raw_key: str) -> bool:
        """Process a single object key. Returns True iff an event was published."""
        parsed = parse_object_key(raw_key)
        if parsed is None:
            self._emit_unknown(raw_key, reason="unparseable_key")
            return False

        if not self._mark_uploaded(parsed):
            return False

        self._publish_audio_uploaded(parsed)
        return True

    def _mark_uploaded(self, parsed: ParsedKey) -> bool:
        """Conditionally flip status from `pending` to `uploaded`.

        Returns True if the record was updated (so we should publish),
        False on:
          - record absent (logged + metric'd as `unknown_record`)
          - record present but status != pending (idempotent re-delivery)
        """
        now_iso = datetime.now(UTC).isoformat()
        try:
            self._table.update_item(
                Key={
                    "pk": _user_pk(parsed.user_id),
                    "sk": _ingestion_sk(parsed.ingestion_id),
                },
                UpdateExpression="SET #s = :uploaded, updated_at = :now",
                ConditionExpression=(
                    "attribute_exists(pk) AND attribute_exists(sk) AND #s = :pending"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":uploaded": "uploaded",
                    ":pending": "pending",
                    ":now": now_iso,
                },
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                # Either the row does not exist, or status != pending.
                # Cheap follow-up read distinguishes the two so we can
                # emit the right signal.
                if not self._record_exists(parsed):
                    self._emit_unknown(
                        f"audio/{parsed.user_id}/{parsed.ingestion_id}/{parsed.filename}",
                        reason="record_missing",
                    )
                else:
                    logger.info(
                        "skipping non-pending record",
                        extra={
                            "user_id": parsed.user_id,
                            "ingestion_id": parsed.ingestion_id,
                        },
                    )
                return False
            raise

    def _record_exists(self, parsed: ParsedKey) -> bool:
        """Return True iff a row exists for this user/ingestion id."""
        response = self._table.get_item(
            Key={
                "pk": _user_pk(parsed.user_id),
                "sk": _ingestion_sk(parsed.ingestion_id),
            }
        )
        return "Item" in response

    def _publish_audio_uploaded(self, parsed: ParsedKey) -> None:
        """Publish the downstream `AudioUploaded` event."""
        detail = {
            "user_id": parsed.user_id,
            "ingestion_id": parsed.ingestion_id,
            "filename": parsed.filename,
        }
        self._events.put_events(
            Entries=[
                {
                    "Source": self._settings.event_source,
                    "DetailType": self._settings.event_detail_type,
                    "Detail": json.dumps(detail),
                    "EventBusName": self._settings.eventbridge_bus_name,
                }
            ]
        )

    def _emit_unknown(self, raw_key: str, *, reason: str) -> None:
        """Record an unmatched-upload metric and log a warning."""
        logger.warning(
            "unknown_record routing miss",
            extra={"raw_key": raw_key, "reason": reason},
        )
        try:
            self._cloudwatch.put_metric_data(
                Namespace=self._settings.metric_namespace,
                MetricData=[
                    {
                        "MetricName": "unknown_record",
                        "Value": 1.0,
                        "Unit": "Count",
                        "Dimensions": [{"Name": "Reason", "Value": reason}],
                    }
                ],
            )
        except ClientError:
            # Metric publication must never fail the batch.
            logger.exception("failed to publish unknown_record metric")


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint.

    `context` is the standard `LambdaContext` object; we do not use it
    today but keep the parameter so the signature matches the runtime
    contract for static analyzers and future enrichment.
    """
    del context  # unused; kept for runtime contract
    settings = load_settings()
    router = EventRouter(settings)
    processed = router.process(event)
    return {"statusCode": 200, "processed": processed}
