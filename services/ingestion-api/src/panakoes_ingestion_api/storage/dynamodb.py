"""DynamoDB read and write helpers for ingestion records.

Schema (provisioned by Terraform):
- pk: `USER#<user_id>` (partition key)
- sk: `INGESTION#<ingestion_id>` (sort key)
- attributes: ingestion_id, user_id, filename, content_type,
  size_bytes, s3_key, status, created_at, updated_at
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_ingestion_api.models import IngestionRecord

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
    return {
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


def _from_dynamo(item: dict[str, Any]) -> IngestionRecord:
    """Convert a DynamoDB item dict back into an `IngestionRecord`.

    DynamoDB returns numeric attributes as `Decimal`; coerce to int so
    Pydantic accepts them without a custom validator.
    """
    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, Decimal):
            cleaned[key] = int(value)
        else:
            cleaned[key] = value
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
