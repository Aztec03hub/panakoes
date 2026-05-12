"""Integration tests for `POST /portal-session`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from panakoes_billing.storage.dynamodb import BillingEventStore
from tests.conftest import (
    TEST_REGION,
    TEST_TABLE_NAME,
    FakeStripeAdapter,
)

# Panakoes-owned return URLs the route accepts.
_ALLOWED_CLOUDFRONT_URL = "https://dmaopcm3hnxog.cloudfront.net/account"
_ALLOWED_APEX_URL = "https://panakoes.com/account"


@pytest.mark.integration
async def test_portal_session_happy_path_with_existing_customer(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """A user with a recorded customer id gets a portal URL, no customer create."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="checkout_completed",
        attributes={"stripe_customer_id": "cus_test_42"},
    )

    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": _ALLOWED_CLOUDFRONT_URL},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://billing.stripe.test/")

    assert len(fake_stripe.portal_calls) == 1
    assert fake_stripe.portal_calls[0]["customer_id"] == "cus_test_42"
    assert fake_stripe.portal_calls[0]["return_url"] == _ALLOWED_CLOUDFRONT_URL
    # No new customer should be created when one is already on file.
    assert fake_stripe.customer_calls == []


@pytest.mark.integration
async def test_portal_session_creates_customer_when_missing(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """No customer id on file => create one, persist it, then open the portal."""
    assert dynamodb_table is not None
    fake_stripe.customer_response = {
        "id": "cus_test_fresh_999",
        "email": "billing-test@panakoes.test",
    }

    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": _ALLOWED_APEX_URL},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://billing.stripe.test/")

    # Stripe Customer.create was called with the JWT email + user_id metadata.
    assert len(fake_stripe.customer_calls) == 1
    assert fake_stripe.customer_calls[0]["email"] == "billing-test@panakoes.test"
    assert fake_stripe.customer_calls[0]["metadata"] == {"user_id": "user_billing_test_123"}

    # The fresh customer id is forwarded to the portal session.
    assert len(fake_stripe.portal_calls) == 1
    assert fake_stripe.portal_calls[0]["customer_id"] == "cus_test_fresh_999"

    # The customer id was persisted so the next call reuses it.
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    assert store.find_customer_id("user_billing_test_123") == "cus_test_fresh_999"


@pytest.mark.integration
async def test_portal_session_requires_auth(async_client: AsyncClient) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.post(
        "/portal-session",
        json={"return_url": _ALLOWED_CLOUDFRONT_URL},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_portal_session_rejects_external_return_url(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """A non-allowlisted origin => 422 and no Stripe call."""
    assert dynamodb_table is not None
    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": "https://evil.example.com/phish"},
    )
    assert response.status_code == 422
    assert fake_stripe.portal_calls == []
    assert fake_stripe.customer_calls == []


@pytest.mark.integration
async def test_portal_session_rejects_http_downgrade(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """An `http://` URL even on an allowlisted host is rejected."""
    assert dynamodb_table is not None
    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": "http://panakoes.com/account"},
    )
    assert response.status_code == 422
    assert fake_stripe.portal_calls == []


@pytest.mark.integration
async def test_portal_session_rejects_subdomain_of_allowlisted_host(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """`https://attacker.panakoes.com.evil.com/...` must be rejected."""
    assert dynamodb_table is not None
    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": "https://panakoes.com.evil.com/account"},
    )
    assert response.status_code == 422
    assert fake_stripe.portal_calls == []


@pytest.mark.integration
async def test_portal_session_rejects_missing_body(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A request with no JSON body => 422 from Pydantic validation."""
    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_portal_session_502_when_stripe_returns_no_url(
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

    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": _ALLOWED_CLOUDFRONT_URL},
    )
    assert response.status_code == 502


@pytest.mark.integration
async def test_portal_session_502_when_customer_create_returns_no_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: object,
) -> None:
    """A Stripe Customer.create with no `id` => 502 and no portal call."""
    assert dynamodb_table is not None
    fake_stripe.customer_response = {"email": "billing-test@panakoes.test"}

    response = await async_client.post(
        "/portal-session",
        headers=auth_headers,
        json={"return_url": _ALLOWED_APEX_URL},
    )
    assert response.status_code == 502
    assert fake_stripe.portal_calls == []
