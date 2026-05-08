"""DynamoDB read and write helpers for notification records.

Schema (provisioned by Terraform):
- pk: `USER#<user_id>` (partition key)
- sk: `NOTIFY#<ulid>` (sort key, ULID lexicographically sorts by time)
- attributes: notification_id, user_id, channel, status, target,
  subject, template, error, attempts, created_at
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_notification.models import NotificationRecord

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


def _user_pk(user_id: str) -> str:
    """Build the partition key for a user's notification records."""
    return f"USER#{user_id}"


def _notification_sk(notification_id: str) -> str:
    """Build the sort key for a single notification record."""
    return f"NOTIFY#{notification_id}"


def _to_dynamo(record: NotificationRecord) -> dict[str, Any]:
    """Convert a Pydantic record to a DynamoDB-ready item dict."""
    item: dict[str, Any] = {
        "pk": _user_pk(record.user_id),
        "sk": _notification_sk(record.notification_id),
        "notification_id": record.notification_id,
        "user_id": record.user_id,
        "channel": record.channel,
        "status": record.status,
        "target": record.target,
        "attempts": record.attempts,
        "created_at": record.created_at.isoformat(),
    }
    if record.subject is not None:
        item["subject"] = record.subject
    if record.template is not None:
        item["template"] = record.template
    if record.error is not None:
        item["error"] = record.error
    return item


def _from_dynamo(item: dict[str, Any]) -> NotificationRecord:
    """Convert a DynamoDB item dict back into a `NotificationRecord`.

    DynamoDB returns numeric attributes as `Decimal`; coerce to int so
    Pydantic accepts them without a custom validator.
    """
    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, Decimal):
            cleaned[key] = int(value)
        else:
            cleaned[key] = value
    return NotificationRecord.model_validate(cleaned)


class NotificationStore:
    """DynamoDB-backed CRUD for notification records.

    The store also exposes descending-by-time queries: ULID sort keys
    are time-ordered, and DynamoDB supports `ScanIndexForward=False` on
    range queries. Newest notifications surface first by default.
    """

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

    def put(self, record: NotificationRecord) -> None:
        """Persist `record` to DynamoDB (upsert semantics)."""
        self._table.put_item(Item=_to_dynamo(record))

    def get(self, user_id: str, notification_id: str) -> NotificationRecord | None:
        """Fetch a single record by user/notification id, or `None`."""
        response = self._table.get_item(
            Key={
                "pk": _user_pk(user_id),
                "sk": _notification_sk(notification_id),
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
    ) -> tuple[list[NotificationRecord], str | None]:
        """Return up to `limit` records for `user_id`, newest first.

        The cursor is the bare `notification_id` of the last item the
        client received. We reconstruct the full sort key internally so
        the wire format stays URL-safe (no `#` separator).
        """
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(_user_pk(user_id))
            & Key("sk").begins_with("NOTIFY#"),
            "Limit": limit,
            "ScanIndexForward": False,  # newest first (ULID sorts by time)
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = {
                "pk": _user_pk(user_id),
                "sk": _notification_sk(cursor),
            }
        response = self._table.query(**kwargs)
        items = [_from_dynamo(dict(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = None
        if isinstance(last_key, dict):
            sk = last_key.get("sk")
            if isinstance(sk, str) and sk.startswith("NOTIFY#"):
                next_cursor = sk[len("NOTIFY#") :]
        return items, next_cursor
