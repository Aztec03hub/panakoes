"""DynamoDB read/write helpers for streaming-session records.

Schema (provisioned by Terraform; see `infra/dev/data/main.tf`):
- `session_id` (hash key): the row's primary identifier (`sess_<ulid>`).
- GSI `UserSessionsIndex`: hash=`user_id`, range=`created_at`. Used to
  list a user's sessions by recency.
- GSI `ActiveSessionsIndex`: hash=`status`, range=`created_at`. Used by
  the idle-timeout reaper Lambda; this service never queries it.
- TTL attribute: `ttl_epoch_seconds` (epoch seconds; renamed from
  `expires_at` in PR #454 to align with the streaming-router schema).
  The Pydantic record field is still named `expires_at` for backward
  compat with the HTTP API surface; only the on-disk DDB attribute
  name changes.

Owner-scoping is enforced at the application layer: read paths fetch
by `session_id`, then check that `user_id` matches the authenticated
caller; write paths use a `ConditionExpression` on `user_id` so a race
on a stolen id cannot corrupt another user's record.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from panakoes_session_manager.models import SessionRecord

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


class SessionNotFoundError(Exception):
    """Raised when a session_id does not exist (or is owned by a different user)."""


class OwnerMismatchError(Exception):
    """Raised when a write would violate the user_id condition on a record.

    Surfaced from `update` and `delete` when the underlying
    `ConditionExpression` rejects the operation. The route layer
    converts this to HTTP 404 to avoid leaking record existence.
    """


def _to_dynamo(record: SessionRecord) -> dict[str, Any]:
    """Convert a Pydantic record to a DynamoDB-ready item dict."""
    item: dict[str, Any] = {
        "session_id": record.session_id,
        "user_id": record.user_id,
        "status": record.status,
        "language": record.language,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "ttl_epoch_seconds": record.expires_at,
    }
    if record.gpu_instance_id is not None:
        item["gpu_instance_id"] = record.gpu_instance_id
    return item


def _from_dynamo(item: dict[str, Any]) -> SessionRecord:
    """Convert a DynamoDB item dict back into a `SessionRecord`.

    DynamoDB returns numeric attributes as `Decimal`; coerce to int so
    Pydantic accepts them without a custom validator.
    """
    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        # Translate the on-disk DDB attribute back to the Pydantic
        # field name. The on-disk name was renamed to
        # `ttl_epoch_seconds` in PR #454; the model field is still
        # `expires_at` for HTTP-API back-compat. Old rows that still
        # have `expires_at` (pre-rename) deserialize fine too.
        target_key = "expires_at" if key == "ttl_epoch_seconds" else key
        if isinstance(value, Decimal):
            cleaned[target_key] = int(value)
        else:
            cleaned[target_key] = value
    return SessionRecord.model_validate(cleaned)


class SessionStore:
    """DynamoDB-backed CRUD for streaming-session records."""

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

    def put(self, record: SessionRecord) -> None:
        """Persist `record` to DynamoDB (insert; rejects collisions).

        We use `attribute_not_exists(session_id)` so that a (vanishingly
        unlikely) ULID collision surfaces loudly instead of silently
        overwriting. Routes generate the id immediately before calling
        `put`, so retries are not expected to hit this branch.
        """
        self._table.put_item(
            Item=_to_dynamo(record),
            ConditionExpression="attribute_not_exists(session_id)",
        )

    def get(self, user_id: str, session_id: str) -> SessionRecord | None:
        """Fetch a session by id, gated on the authenticated owner.

        Returns `None` if the id does not exist OR if the record is
        owned by a different user. The route layer collapses both cases
        to HTTP 404 so we do not leak existence.
        """
        response = self._table.get_item(Key={"session_id": session_id})
        item = response.get("Item")
        if item is None:
            return None
        record = _from_dynamo(dict(item))
        if record.user_id != user_id:
            return None
        return record

    def list_for_user(
        self,
        user_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[SessionRecord], str | None]:
        """Return up to `limit` records for `user_id`, plus a next cursor.

        Queries the `UserSessionsIndex` GSI by `user_id` ordered by
        `created_at` (range key). Cursor is the `session_id` of the last
        item returned; the index lookup re-anchors on the next page by
        passing it back as `ExclusiveStartKey`. We resolve the full
        composite start key via a one-shot `get_item` so the cursor
        wire format stays a bare id.
        """
        kwargs: dict[str, Any] = {
            "IndexName": "UserSessionsIndex",
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "Limit": limit,
            "ScanIndexForward": False,  # newest first
        }
        if cursor is not None:
            anchor = self._table.get_item(Key={"session_id": cursor}).get("Item")
            if anchor is not None and anchor.get("user_id") == user_id:
                kwargs["ExclusiveStartKey"] = {
                    "session_id": cursor,
                    "user_id": user_id,
                    "created_at": anchor["created_at"],
                }
            # Bad/foreign cursor: fall through and start from the top.
            # (Returning an error would let a caller probe whether a
            # session exists by manipulating the cursor.)
        response = self._table.query(**kwargs)
        items = [_from_dynamo(dict(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor: str | None = None
        if isinstance(last_key, dict):
            session_id = last_key.get("session_id")
            if isinstance(session_id, str):
                next_cursor = session_id
        return items, next_cursor

    def update(
        self,
        user_id: str,
        session_id: str,
        updates: dict[str, Any],
    ) -> SessionRecord:
        """Apply `updates` to a session, gated on the owner condition.

        `updates` is a dict of attribute names to new values. The owner
        check is enforced via `ConditionExpression` so the read-modify-
        write window cannot be exploited. Raises `SessionNotFoundError`
        if the row is missing entirely; `OwnerMismatchError` if the row
        exists but is owned by a different user; both surface as 404.
        """
        if not updates:
            raise ValueError("updates dict must not be empty")

        # Confirm existence first so we can distinguish "missing" from
        # "owned by someone else"; both collapse to 404 at the route
        # layer but the audit story is cleaner this way.
        existing = self._table.get_item(Key={"session_id": session_id}).get("Item")
        if existing is None:
            raise SessionNotFoundError(session_id)

        # Build an UpdateExpression dynamically. `#status` is reserved
        # in DynamoDB so we alias it via `ExpressionAttributeNames`.
        set_clauses: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {":expected_user_id": user_id}
        for attr, value in updates.items():
            placeholder = f":{attr}"
            name_alias = f"#{attr}"
            set_clauses.append(f"{name_alias} = {placeholder}")
            names[name_alias] = attr
            values[placeholder] = value
        # `user_id` is also reserved territory in some contexts; alias it.
        names["#user_id"] = "user_id"
        update_expr = "SET " + ", ".join(set_clauses)

        try:
            response = self._table.update_item(
                Key={"session_id": session_id},
                UpdateExpression=update_expr,
                ConditionExpression="#user_id = :expected_user_id",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise OwnerMismatchError(session_id) from exc
            raise
        attrs = response.get("Attributes")
        if attrs is None:
            raise SessionNotFoundError(session_id)
        return _from_dynamo(dict(attrs))
