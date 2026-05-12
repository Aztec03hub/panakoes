"""DynamoDB-backed subscription state store.

The `panakoes-dev-subscriptions` table holds the *current* state of
every Stripe-managed subscription that the platform has seen, keyed by
`tenant_id` (partition) + `subscription_id` (sort). Where the
event-log table (`panakoes-dev-billing-events`) is the append-only
audit trail Stripe produces, this table is the materialised view other
services consult to answer "what plan is tenant X on, right now?"
in a single GetItem.

Schema (provisioned by Terraform in `infra/dev/data/main.tf`):

- pk: ``tenant_id`` (partition key, the Panakoes user / tenant id)
- sk: ``subscription_id`` (sort key, the Stripe ``sub_...`` id)
- attributes: ``plan`` (``free`` | ``pro`` | ``team``), ``status``
  (Stripe subscription status), ``current_period_end`` (ISO 8601),
  ``cancel_at`` (ISO 8601, optional), ``quantity`` (int),
  ``stripe_customer_id`` (optional), ``last_event_id`` (Stripe
  ``evt_...`` id of the last processed event, used for idempotency),
  ``updated_at`` (ISO 8601 of the last write).

Idempotency: every write records the Stripe event id that produced
the row. Replaying a webhook with the same event id is a no-op via a
conditional ``PutItem`` (``attribute_not_exists(last_event_id) OR
last_event_id <> :evt``), so a Stripe retry that arrives after the
first delivery already succeeded simply returns 200 without writing
again. This is the contract the webhook dispatcher relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

import boto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table

Plan = Literal["free", "pro", "team"]
"""The three plans the platform recognises. ``free`` is implicit for
any tenant that has never subscribed; ``pro`` and ``team`` map to the
two paid Stripe Price IDs."""


class SubscriptionStore:
    """DynamoDB-backed current-state store for Stripe subscriptions."""

    def __init__(
        self,
        table_name: str,
        region_name: str = "us-east-1",
        resource: DynamoDBServiceResource | None = None,
    ) -> None:
        """Bind to the named table; ``resource`` is the tests' seam."""
        self._table_name = table_name
        self._region_name = region_name
        if resource is None:
            resource = boto3.resource("dynamodb", region_name=region_name)
        self._table: Table = resource.Table(table_name)

    @property
    def table_name(self) -> str:
        """The DynamoDB table this store writes to."""
        return self._table_name

    def upsert(
        self,
        *,
        tenant_id: str,
        subscription_id: str,
        event_id: str,
        plan: Plan,
        status: str,
        current_period_end: datetime | None = None,
        cancel_at: datetime | None = None,
        quantity: int = 1,
        stripe_customer_id: str | None = None,
    ) -> bool:
        """Idempotently upsert the current state for ``subscription_id``.

        Returns ``True`` if a write occurred and ``False`` if the
        provided ``event_id`` matches the ``last_event_id`` already on
        the row (Stripe replay). Other ``ClientError`` flavours are
        re-raised so callers see real DDB failures.
        """
        item: dict[str, Any] = {
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "plan": plan,
            "status": status,
            "quantity": quantity,
            "last_event_id": event_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if current_period_end is not None:
            item["current_period_end"] = current_period_end.isoformat()
        if cancel_at is not None:
            item["cancel_at"] = cancel_at.isoformat()
        if stripe_customer_id:
            item["stripe_customer_id"] = stripe_customer_id

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(last_event_id) OR last_event_id <> :evt"
                ),
                ExpressionAttributeValues={":evt": event_id},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                # Same event_id already applied: the Stripe retry is a no-op.
                return False
            raise
        return True

    def get_plan(self, tenant_id: str) -> Plan:
        """Return the effective plan for ``tenant_id``.

        Reads every subscription row for the tenant and returns the
        highest-privilege active plan. ``team`` outranks ``pro`` which
        outranks ``free``. A tenant with no rows, or only canceled /
        incomplete rows, gets ``free``. This is the function the auth
        service calls at sign-in time to bake the plan claim into the
        JWT, so it must be cheap and deterministic.
        """
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        )
        items = response.get("Items", [])
        if not items:
            return "free"

        best: Plan = "free"
        rank: dict[str, int] = {"free": 0, "pro": 1, "team": 2}
        for raw in items:
            item = cast("dict[str, Any]", dict(raw))
            status = item.get("status")
            if status not in {"active", "trialing", "past_due"}:
                # past_due still has access by Stripe convention; canceled / unpaid do not.
                continue
            plan = item.get("plan")
            if plan in {"pro", "team", "free"} and rank[plan] > rank[best]:
                best = cast("Plan", plan)
        return best
