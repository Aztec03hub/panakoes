"""Integration tests for `POST /checkout-session`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import TEST_PRICE_PRO, TEST_PRICE_TEAM, FakeStripeAdapter


@pytest.mark.integration
async def test_checkout_pro_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Pro tier returns the canned checkout URL and writes a `checkout_started` audit."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "pro"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["checkout_url"].startswith("https://checkout.stripe.test/")

    # Stripe call captured with the configured Price ID and quantity 1.
    assert len(fake_stripe.checkout_calls) == 1
    call = fake_stripe.checkout_calls[0]
    assert call["price_id"] == TEST_PRICE_PRO
    assert call["quantity"] == 1
    assert call["client_reference_id"] == "user_billing_test_123"

    # Audit recorded.
    events = _audit_memory_store.events
    assert len(events) == 1
    audit = events[0]
    assert audit.action == "billing.checkout_started"
    assert audit.source_service == "billing"
    assert audit.actor_type == "user"


@pytest.mark.integration
async def test_checkout_team_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
) -> None:
    """Team tier with seats >= 3 forwards the seat count as the line-item quantity."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "team", "seats": 5},
    )
    assert response.status_code == 200

    call = fake_stripe.checkout_calls[0]
    assert call["price_id"] == TEST_PRICE_TEAM
    assert call["quantity"] == 5


@pytest.mark.integration
async def test_checkout_team_rejects_under_minimum_seats(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
) -> None:
    """Team tier with seats < 3 returns 422 and does not call Stripe."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "team", "seats": 2},
    )
    assert response.status_code == 422
    assert "seats" in response.json()["detail"]
    assert fake_stripe.checkout_calls == []


@pytest.mark.integration
async def test_checkout_team_rejects_missing_seats(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
) -> None:
    """Team tier with no seats supplied returns 422."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "team"},
    )
    assert response.status_code == 422
    assert fake_stripe.checkout_calls == []


@pytest.mark.integration
async def test_checkout_pro_ignores_seats_field(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
) -> None:
    """Pro tier always uses quantity 1, even if a seat count is provided."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "pro", "seats": 99},
    )
    assert response.status_code == 200
    assert fake_stripe.checkout_calls[0]["quantity"] == 1


@pytest.mark.integration
async def test_checkout_requires_auth(async_client: AsyncClient) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.post(
        "/checkout-session",
        json={"tier": "pro"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_checkout_rejects_bad_token(async_client: AsyncClient) -> None:
    """A garbage Bearer token => 401."""
    response = await async_client.post(
        "/checkout-session",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"tier": "pro"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_checkout_rejects_unknown_tier(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A tier outside the literal set is rejected by Pydantic with 422."""
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "enterprise"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_checkout_502_when_stripe_returns_no_url(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
) -> None:
    """A Stripe response missing the `url` field => 502."""
    fake_stripe.checkout_response = {"id": "cs_no_url"}
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "pro"},
    )
    assert response.status_code == 502


@pytest.mark.integration
async def test_checkout_persists_event_with_session_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    fake_stripe: FakeStripeAdapter,
    dynamodb_table: Any,
) -> None:
    """The `checkout_started` event lands in DDB with the Stripe session id."""
    fake_stripe.checkout_response = {
        "id": "cs_test_session_42",
        "url": "https://checkout.stripe.test/cs_test_session_42",
    }
    response = await async_client.post(
        "/checkout-session",
        headers=auth_headers,
        json={"tier": "pro"},
    )
    assert response.status_code == 200

    items = dynamodb_table.scan()["Items"]
    assert len(items) == 1
    item = items[0]
    assert item["event_type"] == "checkout_started"
    assert item["stripe_session_id"] == "cs_test_session_42"
    assert item["tier"] == "pro"
