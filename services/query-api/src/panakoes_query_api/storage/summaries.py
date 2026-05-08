"""DynamoDB read helpers for summary records.

Schema (forward contract this service publishes for the Summarization
service to follow):
- pk: `USER#<user_id>`
- sk: `SUMMARY#<transcript_id>`
- attributes: transcript_id, user_id, summary_text, tier
  (`fast` | `deep`), model, created_at, updated_at

The sort-key prefix `SUMMARY#` mirrors the `INGESTION#` prefix on the
ingestion table so the same single-table layout could subsume both
record families later if we choose to consolidate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_query_api.models import SummaryRecord

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


def _user_pk(user_id: str) -> str:
    """Build the partition key for a user's summary records."""
    return f"USER#{user_id}"


def _summary_sk(transcript_id: str) -> str:
    """Build the sort key for a single summary record."""
    return f"SUMMARY#{transcript_id}"


def _from_dynamo(item: dict[str, Any]) -> SummaryRecord:
    """Convert a DynamoDB item dict into a `SummaryRecord`."""
    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, Decimal):
            cleaned[key] = int(value)
        else:
            cleaned[key] = value
    return SummaryRecord.model_validate(cleaned)


class SummaryReader:
    """Read-only DynamoDB adapter for summary records."""

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
        """The DynamoDB table this reader queries."""
        return self._table_name

    def get(self, user_id: str, transcript_id: str) -> SummaryRecord | None:
        """Fetch a single summary by user/transcript id, or `None`."""
        response = self._table.get_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _summary_sk(transcript_id),
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
    ) -> tuple[list[SummaryRecord], str | None]:
        """Return up to `limit` summaries for `user_id`, plus a cursor."""
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(_user_pk(user_id))
            & Key("sk").begins_with("SUMMARY#"),
            "Limit": limit,
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = {
                "pk": _user_pk(user_id),
                "sk": _summary_sk(cursor),
            }
        response = self._table.query(**kwargs)
        items = [_from_dynamo(dict(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = None
        if isinstance(last_key, dict):
            sk = last_key.get("sk")
            if isinstance(sk, str) and sk.startswith("SUMMARY#"):
                next_cursor = sk[len("SUMMARY#") :]
        return items, next_cursor
