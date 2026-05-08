"""DynamoDB read helpers for streaming-session records.

Schema (provisioned by Terraform under `infra/dev/data/`,
`aws_dynamodb_table.streaming_sessions`):

- Base table:
    - pk: `session_id` (partition key, no sort key)
- GSI `UserSessionsIndex`:
    - hash_key: `user_id`
    - range_key: `created_at`
    - projection: ALL

The brief asked for `PK = USER#{user_id}, SK = SESSION#{session_id}`,
but the deployed table uses `session_id` as the bare PK with a GSI
for the user-listing access pattern. This module follows the deployed
schema, since the table is owned out-of-band by the Session Manager
and its layout is fixed. The cursor on the wire is the bare
`session_id`; the reader reconstructs the full GSI key locally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_query_api.models import SessionRecord

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


USER_SESSIONS_INDEX = "UserSessionsIndex"


def _from_dynamo(item: dict[str, Any]) -> SessionRecord:
    """Convert a DynamoDB item dict into a `SessionRecord`."""
    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, Decimal):
            cleaned[key] = int(value)
        else:
            cleaned[key] = value
    return SessionRecord.model_validate(cleaned)


class SessionReader:
    """Read-only DynamoDB adapter for streaming-session records."""

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

    def get(self, user_id: str, session_id: str) -> SessionRecord | None:
        """Fetch a session by id, returning `None` if it is not the caller's.

        DynamoDB doesn't support cross-attribute conditional reads in
        `GetItem`, so we fetch by `session_id` and then enforce the
        owner check in code. Cross-user reads collapse to `None`
        which the route layer translates into HTTP 404; the existence
        of the record is never leaked.
        """
        response = self._table.get_item(Key={"session_id": session_id})
        item = response.get("Item")
        if item is None:
            return None
        if item.get("user_id") != user_id:
            return None
        return _from_dynamo(dict(item))

    def list_for_user(
        self,
        user_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[SessionRecord], str | None]:
        """Return up to `limit` sessions for `user_id`, plus a cursor.

        Queries the `UserSessionsIndex` GSI. The cursor on the wire is
        the bare `session_id`; we look the record up to reconstruct the
        full `ExclusiveStartKey` (which needs `session_id`, `user_id`,
        and `created_at` for a GSI scan-resume on a non-KEYS_ONLY
        projection).
        """
        kwargs: dict[str, Any] = {
            "IndexName": USER_SESSIONS_INDEX,
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "Limit": limit,
            "ScanIndexForward": False,
        }
        if cursor is not None:
            anchor = self._table.get_item(Key={"session_id": cursor}).get("Item")
            if anchor is not None and anchor.get("user_id") == user_id:
                kwargs["ExclusiveStartKey"] = {
                    "session_id": cursor,
                    "user_id": user_id,
                    "created_at": anchor.get("created_at"),
                }
        response = self._table.query(**kwargs)
        items = [_from_dynamo(dict(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = None
        if isinstance(last_key, dict):
            session_id = last_key.get("session_id")
            if isinstance(session_id, str):
                next_cursor = session_id
        return items, next_cursor
