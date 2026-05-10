"""DynamoDB read and write helpers for ingestion records.

Schema (provisioned by Terraform):
- pk: `USER#<user_id>` (partition key)
- sk: `INGESTION#<ingestion_id>` (sort key)
- attributes: ingestion_id, user_id, filename, content_type,
  size_bytes, s3_key, status, created_at, updated_at,
  transcript_status (optional), transcript (optional dict),
  transcript_error_message (optional)
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_ingestion_api.models import (
    IngestionRecord,
    TranscriptModel,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


def _user_pk(user_id: str) -> str:
    """Build the partition key for a user's ingestion records."""
    return f"USER#{user_id}"


def _ingestion_sk(ingestion_id: str) -> str:
    """Build the sort key for a single ingestion record."""
    return f"INGESTION#{ingestion_id}"


def _to_dynamo(record: IngestionRecord) -> dict[str, Any]:
    """Convert a Pydantic record to a DynamoDB-ready item dict."""
    item: dict[str, Any] = {
        "pk": _user_pk(record.user_id),
        "sk": _ingestion_sk(record.ingestion_id),
        "ingestion_id": record.ingestion_id,
        "user_id": record.user_id,
        "filename": record.filename,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "s3_key": record.s3_key,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
    if record.transcript_status is not None:
        item["transcript_status"] = record.transcript_status
    if record.transcript is not None:
        item["transcript"] = record.transcript.model_dump(mode="json")
    if record.transcript_error_message is not None:
        item["transcript_error_message"] = record.transcript_error_message
    return item


def _strip_none(value: Any) -> Any:
    """Recursively drop dict keys whose values are ``None``.

    DynamoDB Map attributes reject ``None`` (the resource layer's
    document client only massages it at the top level). We persist the
    transcript as a Map, so optional fields that the Pydantic model
    serializes as ``null`` need to disappear before we hand the dict to
    boto3.
    """
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _decimal_to_native(value: Any) -> Any:
    """Recursively coerce DynamoDB ``Decimal`` to int / float for Pydantic.

    Integers stay integers (so size_bytes does not become a float); any
    Decimal carrying a fractional component (transcript timing data) is
    coerced to ``float``.
    """
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_native(v) for v in value]
    return value


def _from_dynamo(item: dict[str, Any]) -> IngestionRecord:
    """Convert a DynamoDB item dict back into an `IngestionRecord`.

    DynamoDB returns numeric attributes as `Decimal`; coerce to int /
    float so Pydantic accepts them without a custom validator. Walks
    nested dicts / lists so the transcript subtree is normalized too.
    """
    cleaned = {key: _decimal_to_native(value) for key, value in item.items()}
    return IngestionRecord.model_validate(cleaned)


class IngestionStore:
    """DynamoDB-backed CRUD for ingestion records."""

    def __init__(
        self,
        table_name: str,
        region_name: str = "us-east-1",
        resource: DynamoDBServiceResource | None = None,
    ) -> None:
        """Bind to the named table; the resource argument is for tests."""
        self._table_name = table_name
        self._region_name = region_name
        if resource is None:
            resource = boto3.resource("dynamodb", region_name=region_name)
        self._table: Table = resource.Table(table_name)

    @property
    def table_name(self) -> str:
        """The DynamoDB table this store writes to."""
        return self._table_name

    def put(self, record: IngestionRecord) -> None:
        """Persist `record` to DynamoDB (upsert semantics)."""
        self._table.put_item(Item=_to_dynamo(record))

    def get(self, user_id: str, ingestion_id: str) -> IngestionRecord | None:
        """Fetch a single record by user/ingestion id, or `None` if absent."""
        response = self._table.get_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _ingestion_sk(ingestion_id),
            }
        )
        item = response.get("Item")
        if item is None:
            return None
        return _from_dynamo(dict(item))

    def set_transcript_pending(self, user_id: str, ingestion_id: str) -> None:
        """Mark a record's transcript_status as `pending` and clear any error.

        Used both before kicking off a fresh transcription and to reset
        a failed record on retry. Idempotent.
        """
        self._table.update_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _ingestion_sk(ingestion_id),
            },
            UpdateExpression=(
                "SET transcript_status = :s "
                "REMOVE transcript_error_message"
            ),
            ExpressionAttributeValues={":s": "pending"},
        )

    def set_transcript_succeeded(
        self,
        user_id: str,
        ingestion_id: str,
        transcript: TranscriptModel,
    ) -> None:
        """Persist a successful transcript and flip status to `succeeded`."""
        # DynamoDB's TypeSerializer rejects `float` (precision-loss
        # foot-gun) and demands `Decimal`. Round-trip through JSON +
        # parse_float=Decimal so every nested timing value survives, and
        # so naked `None` values are dropped (DynamoDB Maps reject them
        # without the document-client massaging the resource layer
        # provides at the top level only).
        payload = _strip_none(
            json.loads(
                transcript.model_dump_json(),
                parse_float=Decimal,
            )
        )
        self._table.update_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _ingestion_sk(ingestion_id),
            },
            UpdateExpression=(
                "SET transcript_status = :s, transcript = :t "
                "REMOVE transcript_error_message"
            ),
            ExpressionAttributeValues={
                ":s": "succeeded",
                ":t": payload,
            },
        )

    def set_transcript_failed(
        self,
        user_id: str,
        ingestion_id: str,
        error_message: str,
    ) -> None:
        """Flip status to `failed` and persist a short operator error message."""
        self._table.update_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _ingestion_sk(ingestion_id),
            },
            UpdateExpression=(
                "SET transcript_status = :s, transcript_error_message = :m"
            ),
            ExpressionAttributeValues={
                ":s": "failed",
                ":m": error_message[:500],
            },
        )

    def list_for_user(
        self,
        user_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[IngestionRecord], str | None]:
        """Return up to `limit` records for `user_id`, plus a next cursor.

        The cursor is the `ingestion_id` of the last returned item. We
        deliberately keep the wire format URL-safe (a bare UUID, no
        `#` separator) so clients can pass it as a query string without
        percent-encoding pitfalls. The store reconstructs the full sort
        key internally.
        """
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(_user_pk(user_id))
            & Key("sk").begins_with("INGESTION#"),
            "Limit": limit,
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = {
                "pk": _user_pk(user_id),
                "sk": _ingestion_sk(cursor),
            }
        response = self._table.query(**kwargs)
        items = [_from_dynamo(dict(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = None
        if isinstance(last_key, dict):
            sk = last_key.get("sk")
            if isinstance(sk, str) and sk.startswith("INGESTION#"):
                next_cursor = sk[len("INGESTION#") :]
        return items, next_cursor
