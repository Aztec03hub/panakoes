"""Integration tests for `POST /billing/portal`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from panakoes_billing.storage.dynamodb import BillingEventStore
from tests.conftest import (
    TEST_REGION,
    TEST_TABLE_NAME,
    FakeStripeAdapter,
)


@pytest.mark.integration
async def test_portal_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """A user with a recorded customer id gets a portal URL."""
    assert dynamodb_table is not None
    # Seed a checkout_completed event with a customer id for the JWT subject.
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="checkout_completed",
        attributes={"stripe_customer_id": "cus_test_42"},
    )

    response = await async_client.post("/billing/portal", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["portal_url"].startswith("https://billing.stripe.test/")

    assert len(fake_stripe.portal_calls) == 1
    assert fake_stripe.portal_calls[0]["customer_id"] == "cus_test_42"


@pytest.mark.integration
async def test_portal_404_when_no_customer_on_file(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """No customer id recorded => 404 and no Stripe call."""
    assert dynamodb_table is not None
    response = await async_client.post("/billing/portal", headers=auth_headers)
    assert response.status_code == 404
    assert fake_stripe.portal_calls == []


@pytest.mark.integration
async def test_portal_requires_auth(async_client: AsyncClient) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.post("/billing/portal")
    assert response.status_code == 401


@pytest.mark.integration
async def test_portal_502_when_stripe_returns_no_url(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """A Stripe response missing the `url` field => 502."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="checkout_completed",
        attributes={"stripe_customer_id": "cus_test_99"},
    )
    fake_stripe.portal_response = {"id": "bps_no_url"}

    response = await async_client.post("/billing/portal", headers=auth_headers)
    assert response.status_code == 502
