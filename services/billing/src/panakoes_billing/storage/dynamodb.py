"""DynamoDB read and write helpers for billing events.

Schema (provisioned by Terraform in a follow-up slice):

- pk: `USER#<user_id>` (partition key)
- sk: `EVENT#<ulid>` (sort key, time-ordered)
- attributes: event_type, user_id, payload (JSON-serializable dict),
  stripe_customer_id (optional), stripe_subscription_id (optional),
  tier (optional), status (optional), current_period_end (ISO 8601,
  optional), created_at (ISO 8601).

The `event_type` discriminates record shapes:
- `checkout_started`: caller hit POST /checkout-session.
- `checkout_completed`: Stripe `checkout.session.completed` webhook.
- `subscription_updated`: Stripe `customer.subscription.updated`.
- `subscription_deleted`: Stripe `customer.subscription.deleted`.
- `invoice_paid`: Stripe `invoice.paid`.
- `invoice_payment_failed`: Stripe `invoice.payment_failed`.

Reading the current subscription view is a Query for the latest
subscription-bearing event per user, which keeps writes append-only
and avoids racy in-place updates. The next slice replaces this with a
projected materialized view.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import boto3
from boto3.dynamodb.conditions import Key

from panakoes_billing.ulid import new_ulid

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table

# Subset of event types that carry subscription state. The "current
# subscription" reader skips events that do not.
_SUBSCRIPTION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "checkout_completed",
        "subscription_updated",
        "subscription_deleted",
        "invoice_paid",
        "invoice_payment_failed",
    }
)


def _user_pk(user_id: str) -> str:
    """Build the partition key for a user's billing events."""
    return f"USER#{user_id}"


def _event_sk(event_id: str) -> str:
    """Build the sort key for a single billing event."""
    return f"EVENT#{event_id}"


def _coerce_decimals(value: Any) -> Any:
    """Recursively convert DynamoDB `Decimal` values back to int / float.

    DynamoDB returns numeric attributes as `Decimal`. Pydantic accepts
    int and float natively but baulks on Decimal in non-strict mode, so
    we walk the structure once on read.
    """
    if isinstance(value, Decimal):
        # Integer-valued Decimals collapse to int; everything else to float.
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _coerce_decimals(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimals(item) for item in value]
    return value


class BillingEventStore:
    """DynamoDB-backed append-only store for billing events."""

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

    def append(
        self,
        *,
        user_id: str,
        event_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        """Append a billing event for `user_id`. Returns the event id.

        `attributes` is merged into the DDB item (after the protected
        keys), so callers can carry arbitrary Stripe-derived fields
        (subscription id, tier, period end, raw payload) without us
        adding a new method per event type.
        """
        event_id = new_ulid()
        item: dict[str, Any] = {
            "pk": _user_pk(user_id),
            "sk": _event_sk(event_id),
            "event_id": event_id,
            "user_id": user_id,
            "event_type": event_type,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if attributes:
            for key, value in attributes.items():
                # Protect identity / index keys from being overwritten.
                if key in {"pk", "sk", "event_id", "user_id", "event_type", "created_at"}:
                    continue
                item[key] = value
        self._table.put_item(Item=item)
        return event_id

    def latest_event(self, user_id: str) -> dict[str, Any] | None:
        """Return the most recent event for `user_id`, or `None`.

        Used by the audit / debug surface; the public subscription view
        uses `latest_subscription_event` instead so non-subscription
        events do not bury the current state.
        """
        response = self._table.query(
            KeyConditionExpression=Key("pk").eq(_user_pk(user_id))
            & Key("sk").begins_with("EVENT#"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None
        return cast("dict[str, Any]", _coerce_decimals(dict(items[0])))

    def latest_subscription_event(self, user_id: str) -> dict[str, Any] | None:
        """Return the most recent subscription-bearing event, or `None`.

        We page through events newest-first (DynamoDB sort order on the
        sort key) until we find one whose `event_type` is in the set of
        types that carry subscription state. For a user with thousands
        of invoice events this could grow expensive; the next slice
        adds a projected view to make this O(1).
        """
        last_evaluated: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("pk").eq(_user_pk(user_id))
                & Key("sk").begins_with("EVENT#"),
                "ScanIndexForward": False,
                "Limit": 25,
            }
            if last_evaluated is not None:
                kwargs["ExclusiveStartKey"] = last_evaluated
            response = self._table.query(**kwargs)
            for raw in response.get("Items", []):
                event = cast("dict[str, Any]", _coerce_decimals(dict(raw)))
                if event.get("event_type") in _SUBSCRIPTION_EVENT_TYPES:
                    return event
            last_evaluated = response.get("LastEvaluatedKey")
            if last_evaluated is None:
                return None

    def find_customer_id(self, user_id: str) -> str | None:
        """Return the most recent `stripe_customer_id` recorded for `user_id`.

        Used by the Customer Portal route so the same user always lands
        in their own Stripe portal session. Walks events newest-first
        and returns the first non-empty `stripe_customer_id` it finds.
        """
        last_evaluated: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("pk").eq(_user_pk(user_id))
                & Key("sk").begins_with("EVENT#"),
                "ScanIndexForward": False,
                "Limit": 25,
            }
            if last_evaluated is not None:
                kwargs["ExclusiveStartKey"] = last_evaluated
            response = self._table.query(**kwargs)
            for raw in response.get("Items", []):
                event = _coerce_decimals(dict(raw))
                customer_id = event.get("stripe_customer_id")
                if isinstance(customer_id, str) and customer_id:
                    return customer_id
            last_evaluated = response.get("LastEvaluatedKey")
            if last_evaluated is None:
                return None
