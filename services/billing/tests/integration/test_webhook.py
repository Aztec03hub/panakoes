"""Integration tests for `POST /billing/webhook`.

The webhook is unauthenticated at the JWT layer; authentication is
the Stripe-Signature header. We exercise:

- Missing signature header => 400 + audit `webhook_signature_invalid`.
- Invalid signature => 400 + audit `webhook_signature_invalid`.
- Each handled event type => 200 + DDB record + matching audit action.
- Unhandled event types => 200 + no DDB write (Stripe stops retrying).
- Events without a user id => 200 + no DDB write.
- Tier resolution from the price id mirrors the Settings configuration.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import (
    TEST_PRICE_PRO,
    TEST_PRICE_TEAM,
    FakeStripeAdapter,
)


def _checkout_completed_event(
    *,
    user_id: str = "user_billing_test_123",
    customer_id: str = "cus_test_42",
    subscription_id: str = "sub_test_42",
    price_id: str = TEST_PRICE_PRO,
) -> dict[str, Any]:
    """Build a Stripe-shaped `checkout.session.completed` event payload."""
    return {
        "id": "evt_test_checkout",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_completed",
                "client_reference_id": user_id,
                "customer": customer_id,
                "subscription": subscription_id,
                "metadata": {"user_id": user_id},
            }
        },
    }


def _subscription_updated_event(
    *,
    user_id: str = "user_billing_test_123",
    customer_id: str = "cus_test_42",
    subscription_id: str = "sub_test_42",
    price_id: str = TEST_PRICE_TEAM,
    sub_status: str = "active",
    period_end: int = 1_800_000_000,
) -> dict[str, Any]:
    """Build a Stripe-shaped `customer.subscription.updated` event payload."""
    return {
        "id": "evt_test_sub_updated",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": subscription_id,
                "customer": customer_id,
                "status": sub_status,
                "current_period_end": period_end,
                "items": {
                    "data": [
                        {
                            "price": {"id": price_id},
                        }
                    ]
                },
                "metadata": {"user_id": user_id},
            }
        },
    }


@pytest.mark.integration
async def test_webhook_missing_signature_returns_400(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: object,
) -> None:
    """No `Stripe-Signature` header => 400 + audit log entry."""
    assert dynamodb_table is not None
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
    )
    assert response.status_code == 400
    assert "Stripe-Signature" in response.json()["detail"]
    assert fake_stripe.webhook_calls == []
    actions = [event.action for event in _audit_memory_store.events]
    assert actions == ["billing.webhook_signature_invalid"]


@pytest.mark.integration
async def test_webhook_blank_signature_returns_400(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: object,
) -> None:
    """A whitespace-only signature header is treated as missing."""
    assert dynamodb_table is not None
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "   "},
    )
    assert response.status_code == 400
    assert fake_stripe.webhook_calls == []


@pytest.mark.integration
async def test_webhook_invalid_signature_returns_400(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: object,
) -> None:
    """A bad signature => 400 + audit + no DDB write."""
    assert dynamodb_table is not None
    fake_stripe.fail_signature_with = "bad signature"
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=garbage"},
    )
    assert response.status_code == 400
    assert "invalid Stripe webhook signature" in response.json()["detail"]
    actions = [event.action for event in _audit_memory_store.events]
    assert actions == ["billing.webhook_signature_invalid"]


@pytest.mark.integration
async def test_webhook_checkout_completed_writes_event_and_audit(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: Any,
) -> None:
    """A valid `checkout.session.completed` writes a `checkout_completed` event."""
    payload_dict = _checkout_completed_event()
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    items = dynamodb_table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "checkout_completed"
    assert items[0]["stripe_customer_id"] == "cus_test_42"

    audit_actions = [event.action for event in _audit_memory_store.events]
    assert audit_actions == ["billing.subscription_changed"]


@pytest.mark.integration
async def test_webhook_subscription_updated_resolves_team_tier(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: Any,
) -> None:
    """A `customer.subscription.updated` with the team Price ID stores `tier=team`."""
    payload_dict = _subscription_updated_event(price_id=TEST_PRICE_TEAM)
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert len(items) == 1
    item = items[0]
    assert item["event_type"] == "subscription_updated"
    assert item["tier"] == "team"
    assert item["status"] == "active"
    assert item["stripe_price_id"] == TEST_PRICE_TEAM
    # current_period_end is rendered ISO 8601 with timezone.
    parsed = datetime.fromisoformat(item["current_period_end"])
    assert parsed.tzinfo is not None


@pytest.mark.integration
async def test_webhook_subscription_updated_resolves_pro_tier(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A `customer.subscription.updated` with the pro Price ID stores `tier=pro`."""
    payload_dict = _subscription_updated_event(price_id=TEST_PRICE_PRO)
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert items[0]["tier"] == "pro"


@pytest.mark.integration
async def test_webhook_subscription_deleted(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: Any,
) -> None:
    """A `customer.subscription.deleted` writes the deletion event."""
    payload_dict = {
        "id": "evt_test_sub_del",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_dead",
                "customer": "cus_test_dead",
                "status": "canceled",
                "metadata": {"user_id": "user_billing_test_123"},
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert items[0]["event_type"] == "subscription_deleted"
    audit_actions = [event.action for event in _audit_memory_store.events]
    assert audit_actions == ["billing.subscription_changed"]


@pytest.mark.integration
async def test_webhook_invoice_paid(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: Any,
) -> None:
    """`invoice.paid` writes a payment-success event and the matching audit."""
    payload_dict = {
        "id": "evt_test_invoice_paid",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test",
                "customer": "cus_test",
                "subscription": "sub_test",
                "metadata": {"user_id": "user_billing_test_123"},
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert items[0]["event_type"] == "invoice_paid"
    audit_actions = [event.action for event in _audit_memory_store.events]
    assert audit_actions == ["billing.payment_succeeded"]


@pytest.mark.integration
async def test_webhook_invoice_payment_failed(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
    dynamodb_table: Any,
) -> None:
    """`invoice.payment_failed` writes a failure event and the matching audit."""
    payload_dict = {
        "id": "evt_test_invoice_failed",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_test_failed",
                "customer": "cus_test",
                "subscription": "sub_test",
                "metadata": {"user_id": "user_billing_test_123"},
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert items[0]["event_type"] == "invoice_payment_failed"
    audit_actions = [event.action for event in _audit_memory_store.events]
    assert audit_actions == ["billing.payment_failed"]


@pytest.mark.integration
async def test_webhook_unhandled_event_returns_200_no_write(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """Stripe events outside the curated set return 200 and write nothing."""
    payload_dict = {
        "id": "evt_test_unhandled",
        "type": "customer.created",
        "data": {"object": {"id": "cus_x"}},
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert dynamodb_table.scan()["Items"] == []


@pytest.mark.integration
async def test_webhook_event_without_user_id_returns_200_no_write(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A handled event that lacks a Panakoes user id is skipped (200, no write)."""
    payload_dict = {
        "id": "evt_test_orphan",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_orphan",
                "customer": "cus_orphan",
                # No client_reference_id, no metadata.user_id.
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert dynamodb_table.scan()["Items"] == []


@pytest.mark.integration
async def test_webhook_uses_metadata_user_id_when_no_client_ref(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """If `client_reference_id` is missing, fall back to `metadata.user_id`."""
    payload_dict = {
        "id": "evt_test_meta_only",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_meta_only",
                "customer": "cus_meta",
                "metadata": {"user_id": "user_from_metadata"},
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["pk"] == "USER#user_from_metadata"


@pytest.mark.integration
async def test_webhook_event_with_no_type_returns_200_ignored(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A signed event missing the `type` field is acknowledged but skipped."""
    fake_stripe.next_event_override = {"id": "evt_test_no_type"}  # no `type` key
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert dynamodb_table.scan()["Items"] == []


@pytest.mark.integration
async def test_webhook_handles_missing_data_object(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A handled event missing `data.object` skips quietly with 200."""
    fake_stripe.next_event_override = {
        "id": "evt_no_data",
        "type": "invoice.paid",
    }
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert dynamodb_table.scan()["Items"] == []


@pytest.mark.integration
async def test_webhook_handles_data_object_not_dict(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A handled event whose `data.object` is not a dict still drains with 200."""
    fake_stripe.next_event_override = {
        "id": "evt_bad_object",
        "type": "invoice.paid",
        "data": {"object": "not-a-dict"},
    }
    response = await async_client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert dynamodb_table.scan()["Items"] == []


@pytest.mark.integration
async def test_webhook_event_with_unknown_price_id_does_not_set_tier(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A subscription event with an unconfigured Price ID stores no `tier`."""
    payload_dict = _subscription_updated_event(price_id="price_test_unknown")
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert items[0]["stripe_price_id"] == "price_test_unknown"
    assert "tier" not in items[0]


@pytest.mark.integration
async def test_webhook_event_with_no_items_does_not_set_price(
    async_client: AsyncClient,
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """A subscription event without `items` works and stores no price id."""
    payload_dict: dict[str, Any] = {
        "id": "evt_no_items",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_no_items",
                "customer": "cus_no_items",
                "status": "active",
                "metadata": {"user_id": "user_billing_test_123"},
            }
        },
    }
    response = await async_client.post(
        "/billing/webhook",
        content=json.dumps(payload_dict).encode(),
        headers={"Stripe-Signature": "t=1,v1=valid"},
    )
    assert response.status_code == 200
    items = dynamodb_table.scan()["Items"]
    assert "stripe_price_id" not in items[0]
    assert items[0]["status"] == "active"
