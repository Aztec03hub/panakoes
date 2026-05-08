"""Integration tests for `GET /billing/subscription`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from panakoes_billing.storage.dynamodb import BillingEventStore
from tests.conftest import TEST_REGION, TEST_TABLE_NAME


@pytest.mark.integration
async def test_subscription_returns_empty_for_new_user(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    dynamodb_table: object,
) -> None:
    """A user with no events sees the all-null SubscriptionView."""
    assert dynamodb_table is not None
    response = await async_client.get("/billing/subscription", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {"tier": None, "status": None, "current_period_end": None}


@pytest.mark.integration
async def test_subscription_returns_latest_subscription_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    dynamodb_table: object,
) -> None:
    """The view reflects the most recent subscription event."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="subscription_updated",
        attributes={
            "tier": "pro",
            "status": "active",
            "current_period_end": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
        },
    )

    response = await async_client.get("/billing/subscription", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "pro"
    assert body["status"] == "active"
    assert body["current_period_end"].startswith("2026-06-01")


@pytest.mark.integration
async def test_subscription_drops_unknown_tier(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    dynamodb_table: object,
) -> None:
    """A stored tier outside the literal set surfaces as None."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="subscription_updated",
        attributes={"tier": "legacy_enterprise", "status": "active"},
    )
    response = await async_client.get("/billing/subscription", headers=auth_headers)
    body = response.json()
    assert body["tier"] is None
    assert body["status"] == "active"


@pytest.mark.integration
async def test_subscription_handles_corrupt_period_end(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    dynamodb_table: object,
) -> None:
    """A corrupt `current_period_end` does not 500 the read."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="invoice_paid",
        attributes={
            "tier": "pro",
            "status": "active",
            "current_period_end": "not-a-date",
        },
    )
    response = await async_client.get("/billing/subscription", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["current_period_end"] is None


@pytest.mark.integration
async def test_subscription_handles_non_string_status(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    dynamodb_table: object,
) -> None:
    """A non-string status surfaces as None instead of 500ing."""
    assert dynamodb_table is not None
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="user_billing_test_123",
        event_type="invoice_paid",
        attributes={"tier": "pro", "status": 12345},
    )
    response = await async_client.get("/billing/subscription", headers=auth_headers)
    body = response.json()
    assert body["status"] is None


@pytest.mark.integration
async def test_subscription_requires_auth(async_client: AsyncClient) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.get("/billing/subscription")
    assert response.status_code == 401
